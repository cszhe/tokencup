"""Chess rules layer, wrapping python-chess.

The authoritative game state is the ordered list of UCI moves, NOT a FEN string.
Everything here rebuilds a board by replaying those moves. See `build_board` for
why that matters -- it is the single most important invariant in this codebase.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import chess
import chess.pgn

# Result strings, matching the `games.result` column.
WHITE_WINS = "1-0"
BLACK_WINS = "0-1"
DRAW = "1/2-1/2"

# Termination reasons, matching the `games.termination` column.
CHECKMATE = "checkmate"
STALEMATE = "stalemate"
INSUFFICIENT_MATERIAL = "insufficient_material"
FIFTY_MOVES = "fifty_moves"
REPETITION = "repetition"
RESIGNATION = "resignation"
FORFEIT = "forfeit"

STARTING_FEN = chess.STARTING_FEN


class IllegalMoveError(Exception):
    """The move is well-formed but not legal in the current position."""


class UnparseableMoveError(Exception):
    """The move string could not be parsed as SAN or UCI."""


def build_board(uci_moves: list[str]) -> chess.Board:
    """Rebuild the authoritative board by replaying moves from the start.

    This replay is mandatory, not an optimization choice. A `chess.Board`
    constructed from a FEN has an empty move stack, so:

      * `is_repetition()` cannot see prior positions -- threefold repetition
        silently never fires;
      * the halfmove clock is present in the FEN but the position history that
        `can_claim_draw()` needs is not.

    So any rules decision must be made on a replayed board. `games.fen` is a
    denormalized convenience column for the frontend and the judge only.
    """
    board = chess.Board()
    for uci in uci_moves:
        board.push_uci(uci)
    return board


def parse_move(board: chess.Board, move_str: str) -> chess.Move:
    """Parse a move given in SAN (`e4`, `Nf3`, `O-O`) or UCI (`e2e4`) notation.

    Raises UnparseableMoveError if the string is not a move in either notation,
    and IllegalMoveError if it parses but is not legal in this position (which
    also covers moving out of turn -- the side to move comes from the board).
    """
    move_str = move_str.strip()
    if not move_str:
        raise UnparseableMoveError("move is empty")

    # The null move ("0000") parses cleanly in both notations but is never a real
    # game move -- without this guard an agent could pass its turn.
    if move_str == "0000":
        raise UnparseableMoveError("'0000' (null move) is not a legal game move")

    # SAN first: it is what agents most often produce, and `parse_san` raises
    # IllegalMoveError itself for well-formed but illegal SAN.
    try:
        move = board.parse_san(move_str)
        if move not in board.legal_moves:  # defence in depth
            raise IllegalMoveError(f"'{move_str}' is not legal in this position")
        return move
    except chess.IllegalMoveError:
        raise IllegalMoveError(
            f"'{move_str}' is not legal in this position "
            f"({'white' if board.turn else 'black'} to move)"
        ) from None
    except chess.AmbiguousMoveError:
        raise IllegalMoveError(
            f"'{move_str}' is ambiguous -- more than one piece can make that move"
        ) from None
    except (chess.InvalidMoveError, ValueError):
        pass  # not SAN; fall through to UCI

    try:
        move = chess.Move.from_uci(move_str)
    except chess.InvalidMoveError:
        raise UnparseableMoveError(
            f"'{move_str}' is not valid SAN or UCI notation"
        ) from None

    # A null move is well-formed UCI ("0000") but never a legal game move.
    if move not in board.legal_moves:
        raise IllegalMoveError(
            f"'{move_str}' is not legal in this position "
            f"({'white' if board.turn else 'black'} to move)"
        )
    return move


@dataclass(frozen=True)
class GameOver:
    result: str
    termination: str


def detect_game_over(board: chess.Board) -> GameOver | None:
    """Return the outcome if the game has ended, else None.

    `claim_draw=True` is required: without it python-chess reports neither
    threefold repetition nor the fifty-move rule, since both are claims a player
    makes rather than automatic terminations.
    """
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return None

    termination_map = {
        chess.Termination.CHECKMATE: CHECKMATE,
        chess.Termination.STALEMATE: STALEMATE,
        chess.Termination.INSUFFICIENT_MATERIAL: INSUFFICIENT_MATERIAL,
        chess.Termination.FIFTY_MOVES: FIFTY_MOVES,
        chess.Termination.SEVENTYFIVE_MOVES: FIFTY_MOVES,
        chess.Termination.THREEFOLD_REPETITION: REPETITION,
        chess.Termination.FIVEFOLD_REPETITION: REPETITION,
    }
    return GameOver(
        result=outcome.result(),
        termination=termination_map.get(outcome.termination, "draw"),
    )


@dataclass(frozen=True)
class AppliedMove:
    """The result of legally applying one move to a position."""

    san: str
    uci: str
    fen_after: str
    side: str  # 'w' or 'b' -- the side that made this move
    game_over: GameOver | None


def apply_move(board: chess.Board, move_str: str) -> AppliedMove:
    """Parse and apply `move_str`, mutating `board`.

    Raises UnparseableMoveError (-> HTTP 400) or IllegalMoveError (-> HTTP 409).
    """
    move = parse_move(board, move_str)

    # SAN must be computed BEFORE the push: it is relative to the position the
    # move is made from, and disambiguation depends on the other pieces still
    # standing where they are.
    san = board.san(move)
    side = "w" if board.turn == chess.WHITE else "b"

    board.push(move)

    return AppliedMove(
        san=san,
        uci=move.uci(),
        fen_after=board.fen(),
        side=side,
        game_over=detect_game_over(board),
    )


def to_pgn(
    uci_moves: list[str],
    white_name: str,
    black_name: str,
    result: str | None = None,
    game_id: str | None = None,
) -> str:
    """Render the game as a PGN string."""
    game = chess.pgn.Game()
    game.headers["Event"] = "TokenCup"
    game.headers["White"] = white_name
    game.headers["Black"] = black_name
    game.headers["Result"] = result or "*"
    if game_id:
        game.headers["Site"] = game_id

    node = game
    board = chess.Board()
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)

    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    return game.accept(exporter)


def turn_of(fen: str) -> str:
    """The side to move in a FEN: 'w' or 'b'."""
    return fen.split()[1]


def legal_moves_san(board: chess.Board) -> list[str]:
    """All legal moves in SAN, for offering to player agents."""
    return [board.san(m) for m in board.legal_moves]


def read_pgn_moves(pgn_text: str) -> list[str]:
    """Extract UCI moves from a PGN string (used by tests and tooling)."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return []
    return [m.uci() for m in game.mainline_moves()]
