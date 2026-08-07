"""TokenCup API: a chess server for AI agents.

Games are driven by a trusted judge agent, which is the only client that submits
moves. The server owns chess rules and history; policy (timeouts, illegal-move
retries, forfeits) lives in the judge.

Endpoints that touch the database are plain `def`, so FastAPI runs them in its
threadpool -- PyMySQL is a blocking driver.
"""

from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import chess_logic as cl
import db as dbq
from config import ConfigError, load_config
from db import Database
from schemas import (
    AdjudicateRequest,
    CreateGameRequest,
    GameDetail,
    GameSummary,
    MoveRequest,
    MoveOut,
    ResignRequest,
)

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

# Populated at startup; module-level so tests can swap it out.
database: Database | None = None


def get_db() -> Database:
    if database is None:  # pragma: no cover -- only if startup was skipped
        raise HTTPException(status_code=503, detail="database not initialised")
    return database


@asynccontextmanager
async def lifespan(app: FastAPI):
    global database
    if database is None:
        config = load_config(os.environ.get("TOKENCUP_CONFIG"))
        database = Database(config)
    database.init_schema()
    yield


app = FastAPI(title="TokenCup", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------


def _summary(row: dict, move_count: int) -> GameSummary:
    return GameSummary(
        id=row["id"],
        match_id=row["match_id"],
        white_name=row["white_name"],
        black_name=row["black_name"],
        status=row["status"],
        result=row["result"],
        termination=row["termination"],
        fen=row["fen"],
        turn=cl.turn_of(row["fen"]),
        move_count=move_count,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _detail(cur, row: dict) -> GameDetail:
    moves = dbq.get_moves(cur, row["id"])
    return GameDetail(
        **_summary(row, len(moves)).model_dump(),
        pgn=row["pgn"],
        moves=[MoveOut(**m) for m in moves],
    )


def _load_or_404(cur, game_id: str, for_update: bool = False) -> dict:
    row = dbq.get_game(cur, game_id, for_update=for_update)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such game: {game_id}")
    return row


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@app.post("/games", response_model=GameDetail, status_code=201)
def create_game(req: CreateGameRequest, database: Database = Depends(get_db)):
    """Create a game in the standard starting position."""
    with database.transaction() as cur:
        pgn = cl.to_pgn([], req.white_name, req.black_name)
        game_id = dbq.create_game(
            cur, req.white_name, req.black_name, cl.STARTING_FEN, pgn, req.match_id
        )
        return _detail(cur, _load_or_404(cur, game_id))


@app.get("/games", response_model=list[GameSummary])
def list_games(
    status: str | None = Query(default=None, pattern="^(active|finished)$"),
    limit: int = Query(default=50, ge=1, le=200),
    database: Database = Depends(get_db),
):
    """List games, newest first."""
    with database.transaction() as cur:
        rows = dbq.list_games(cur, status=status, limit=limit)
        counts = dbq.count_moves_for(cur, [row["id"] for row in rows])
        return [_summary(row, counts[row["id"]]) for row in rows]


@app.get("/games/{game_id}", response_model=GameDetail)
def get_game(game_id: str, database: Database = Depends(get_db)):
    """Full state of one game, including move history."""
    with database.transaction() as cur:
        return _detail(cur, _load_or_404(cur, game_id))


@app.post("/games/{game_id}/moves", response_model=GameDetail)
def submit_move(game_id: str, req: MoveRequest, database: Database = Depends(get_db)):
    """Submit a move. The judge calls this.

    The whole check-and-apply runs in one transaction with the game row locked,
    so two simultaneous submissions cannot both succeed against the same
    position.
    """
    with database.transaction() as cur:
        row = _load_or_404(cur, game_id, for_update=True)

        if row["status"] != "active":
            raise HTTPException(
                status_code=409,
                detail=f"game is already finished ({row['result']}, {row['termination']})",
            )

        # Rebuild the authoritative board from move history -- never from
        # games.fen, which is a denormalised convenience column and carries no
        # position history for repetition or fifty-move detection.
        uci_moves = dbq.get_uci_moves(cur, game_id)
        board = cl.build_board(uci_moves)

        try:
            applied = cl.apply_move(board, req.move)
        except cl.UnparseableMoveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except cl.IllegalMoveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

        ply = len(uci_moves) + 1
        dbq.insert_move(
            cur, game_id, ply, applied.side, applied.san, applied.uci, applied.fen_after
        )

        over = applied.game_over
        status = "finished" if over else "active"
        result = over.result if over else None
        termination = over.termination if over else None
        pgn = cl.to_pgn(
            uci_moves + [applied.uci],
            row["white_name"],
            row["black_name"],
            result=result,
            game_id=game_id,
        )
        dbq.update_game_state(
            cur, game_id, applied.fen_after, pgn, status, result, termination
        )

        return _detail(cur, _load_or_404(cur, game_id))


@app.post("/games/{game_id}/resign", response_model=GameDetail)
def resign(game_id: str, req: ResignRequest, database: Database = Depends(get_db)):
    """End a game early: voluntary resignation, or a judge-adjudicated forfeit."""
    with database.transaction() as cur:
        row = _load_or_404(cur, game_id, for_update=True)

        if row["status"] != "active":
            raise HTTPException(
                status_code=409, detail="game is already finished"
            )

        result = cl.BLACK_WINS if req.player == "white" else cl.WHITE_WINS
        uci_moves = dbq.get_uci_moves(cur, game_id)
        pgn = cl.to_pgn(
            uci_moves, row["white_name"], row["black_name"], result=result, game_id=game_id
        )
        dbq.update_game_state(
            cur, game_id, row["fen"], pgn, "finished", result, req.termination
        )
        return _detail(cur, _load_or_404(cur, game_id))


@app.post("/games/{game_id}/adjudicate", response_model=GameDetail)
def adjudicate(game_id: str, req: AdjudicateRequest, database: Database = Depends(get_db)):
    """End a game with an outcome the rules did not reach on their own."""
    with database.transaction() as cur:
        row = _load_or_404(cur, game_id, for_update=True)

        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="game is already finished")

        uci_moves = dbq.get_uci_moves(cur, game_id)
        pgn = cl.to_pgn(
            uci_moves,
            row["white_name"],
            row["black_name"],
            result=req.result,
            game_id=game_id,
        )
        dbq.update_game_state(
            cur, game_id, row["fen"], pgn, "finished", req.result, req.termination
        )
        return _detail(cur, _load_or_404(cur, game_id))


@app.get("/health")
def health(database: Database = Depends(get_db)):
    database.ping()
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")


def main() -> None:
    global database
    parser = argparse.ArgumentParser(description="Run the TokenCup server")
    parser.add_argument("--config", help="path to config.toml")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}")

    database = Database(config)

    import uvicorn

    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
