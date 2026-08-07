"""The judge: the only client that talks to the TokenCup API.

The server owns chess rules; the judge owns policy. It asks each player for a
move, submits it, and decides what to do when a player misbehaves:

  * per-move timeout for a slow or hung agent
  * an illegal-move retry budget, feeding the server's rejection back to the
    agent so it can correct itself
  * forfeit (via the resign endpoint) when either budget is exhausted
"""

from __future__ import annotations

import concurrent.futures
import sys
from dataclasses import dataclass, field
from pathlib import Path

import chess
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import chess_logic as cl  # noqa: E402
from players import PlayerAgent, Position  # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


@dataclass
class JudgeConfig:
    base_url: str = DEFAULT_BASE_URL
    max_retries: int = 3  # illegal-move attempts allowed per turn
    move_timeout: float = 30.0  # seconds a player may take per move
    max_plies: int = 600  # hard stop, so a pathological game cannot run forever
    verbose: bool = True


@dataclass
class MatchResult:
    game_id: str
    result: str | None
    termination: str | None
    plies: int
    forfeited_by: str | None = None
    pgn: str | None = None
    events: list[str] = field(default_factory=list)


class PlayerTimeout(Exception):
    """The player did not return a move within the allotted time."""


class Judge:
    def __init__(self, config: JudgeConfig | None = None):
        self.config = config or JudgeConfig()
        self._client = httpx.Client(base_url=self.config.base_url, timeout=30.0)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- helpers -----------------------------------------------------------

    def _log(self, events: list[str], message: str) -> None:
        events.append(message)
        if self.config.verbose:
            print(message, flush=True)

    def _ask(self, player: PlayerAgent, position: Position, rejected, reason) -> str:
        """Call the player with a timeout so a hung agent cannot stall the game."""
        future = self._pool.submit(player.get_move, position, rejected, reason)
        try:
            move = future.result(timeout=self.config.move_timeout)
        except concurrent.futures.TimeoutError:
            # The worker thread may still be running; the pool is per-judge and
            # discarded at the end of the match, so this leaks nothing lasting.
            raise PlayerTimeout(
                f"{player.name} exceeded {self.config.move_timeout}s"
            ) from None
        if not isinstance(move, str):
            raise PlayerTimeout(f"{player.name} returned {type(move).__name__}, not a move")
        return move

    def _position(self, state: dict, color: str) -> Position:
        board = cl.build_board([m["uci"] for m in state["moves"]])
        return Position(
            fen=board.fen(),
            board=board,
            legal_moves=cl.legal_moves_san(board),
            history=[m["san"] for m in state["moves"]],
            color=color,
        )

    # -- API calls ---------------------------------------------------------

    def create_game(self, white: str, black: str, match_id: str | None = None) -> dict:
        r = self._client.post(
            "/games",
            json={"white_name": white, "black_name": black, "match_id": match_id},
        )
        r.raise_for_status()
        return r.json()

    def get_game(self, game_id: str) -> dict:
        r = self._client.get(f"/games/{game_id}")
        r.raise_for_status()
        return r.json()

    def _resign(self, game_id: str, player: str, termination: str) -> dict:
        r = self._client.post(
            f"/games/{game_id}/resign",
            json={"player": player, "termination": termination},
        )
        r.raise_for_status()
        return r.json()

    def _adjudicate(self, game_id: str, result: str, termination: str) -> dict:
        r = self._client.post(
            f"/games/{game_id}/adjudicate",
            json={"result": result, "termination": termination},
        )
        r.raise_for_status()
        return r.json()

    # -- the game loop -----------------------------------------------------

    def play(
        self,
        white: PlayerAgent,
        black: PlayerAgent,
        game_id: str | None = None,
        match_id: str | None = None,
    ) -> MatchResult:
        """Run one game to completion and return the outcome."""
        events: list[str] = []

        state = (
            self.get_game(game_id)
            if game_id
            else self.create_game(white.name, black.name, match_id)
        )
        game_id = state["id"]
        self._log(events, f"game {game_id}: {white.name} (W) vs {black.name} (B)")

        forfeited_by: str | None = None

        while state["status"] == "active":
            if len(state["moves"]) >= self.config.max_plies:
                # A backstop, not a chess rule: adjudicate a draw rather than
                # pinning a loss on whoever happens to be on move.
                self._log(events, f"ply limit {self.config.max_plies} reached; drawing")
                state = self._adjudicate(game_id, cl.DRAW, "ply_limit")
                break

            color = "white" if state["turn"] == "w" else "black"
            player = white if color == "white" else black
            position = self._position(state, color)

            outcome = self._take_turn(game_id, player, position, color, events)
            if outcome is None:
                forfeited_by = color
                state = self._resign(game_id, color, "forfeit")
                self._log(events, f"{player.name} forfeits ({color})")
                break
            state = outcome

        result = MatchResult(
            game_id=game_id,
            result=state["result"],
            termination=state["termination"],
            plies=len(state["moves"]),
            forfeited_by=forfeited_by,
            pgn=state.get("pgn"),
            events=events,
        )
        self._log(
            events,
            f"finished: {result.result} by {result.termination} after {result.plies} plies",
        )
        return result

    def _take_turn(
        self,
        game_id: str,
        player: PlayerAgent,
        position: Position,
        color: str,
        events: list[str],
    ) -> dict | None:
        """Get a move from `player` and submit it. Returns None if it forfeits."""
        rejected: str | None = None
        reason: str | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                move = self._ask(player, position, rejected, reason)
            except PlayerTimeout as exc:
                self._log(events, f"timeout: {exc}")
                return None

            response = self._client.post(f"/games/{game_id}/moves", json={"move": move})
            if response.is_success:
                return response.json()

            if response.status_code not in (400, 409):
                response.raise_for_status()

            detail = response.json().get("detail", "rejected")
            # A 409 on a finished game is not the player's fault -- stop retrying.
            if "already finished" in str(detail):
                self._log(events, f"game already finished: {detail}")
                return self.get_game(game_id)

            rejected, reason = move, str(detail)
            self._log(
                events,
                f"  rejected {move!r} from {player.name} "
                f"(attempt {attempt}/{self.config.max_retries}): {detail}",
            )

        return None


def play_match(
    white: PlayerAgent,
    black: PlayerAgent,
    config: JudgeConfig | None = None,
    match_id: str | None = None,
) -> MatchResult:
    """Convenience wrapper: run a single game and clean up."""
    with Judge(config) as judge:
        return judge.play(white, black, match_id=match_id)
