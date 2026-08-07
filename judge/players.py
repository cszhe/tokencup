"""Player agents.

A player never talks to the server. The judge hands it a position and asks for a
move; whatever it returns is submitted on its behalf. Implement `get_move` and a
new kind of agent drops straight into a match.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import chess


@dataclass
class Position:
    """Everything a player is told about the current position."""

    fen: str
    board: chess.Board
    legal_moves: list[str]  # SAN
    history: list[str]  # SAN, in order
    color: str  # 'white' or 'black'


class PlayerAgent:
    """Interface every player implements."""

    name: str = "player"

    def get_move(
        self,
        position: Position,
        rejected: str | None = None,
        reason: str | None = None,
    ) -> str:
        """Return a move in SAN or UCI.

        On a retry, `rejected` is the move the server refused and `reason` is
        its explanation, so the agent can correct itself.
        """
        raise NotImplementedError


class RandomBot(PlayerAgent):
    """Reference implementation: picks a uniformly random legal move."""

    def __init__(self, name: str = "RandomBot", seed: int | None = None):
        self.name = name
        self._rng = random.Random(seed)

    def get_move(self, position, rejected=None, reason=None) -> str:
        return self._rng.choice(position.legal_moves)


class GreedyBot(PlayerAgent):
    """Slightly stronger: grabs the most valuable capture, else plays randomly.

    Useful for sanity-checking that games are not purely noise.
    """

    VALUES = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 0,
    }

    def __init__(self, name: str = "GreedyBot", seed: int | None = None):
        self.name = name
        self._rng = random.Random(seed)

    def get_move(self, position, rejected=None, reason=None) -> str:
        board = position.board
        best, best_value = [], 0
        for move in board.legal_moves:
            board.push(move)
            mate = board.is_checkmate()
            board.pop()
            if mate:
                return board.san(move)
            captured = board.piece_at(move.to_square)
            value = self.VALUES[captured.piece_type] if captured else 0
            if value > best_value:
                best, best_value = [move], value
            elif value == best_value:
                best.append(move)
        return board.san(self._rng.choice(best))


class AlwaysIllegalBot(PlayerAgent):
    """Test double: always returns garbage, so it must forfeit."""

    def __init__(self, name: str = "BrokenBot"):
        self.name = name

    def get_move(self, position, rejected=None, reason=None) -> str:
        return "Zz9"


BUILTIN_PLAYERS = {
    "random": RandomBot,
    "greedy": GreedyBot,
    "illegal": AlwaysIllegalBot,
}


def make_player(kind: str, name: str | None = None, seed: int | None = None) -> PlayerAgent:
    """Build a built-in player by kind name."""
    if kind not in BUILTIN_PLAYERS:
        raise ValueError(
            f"unknown player kind '{kind}' (choose from {', '.join(BUILTIN_PLAYERS)})"
        )
    cls = BUILTIN_PLAYERS[kind]
    if cls is AlwaysIllegalBot:
        return cls(name or "BrokenBot")
    return cls(name or cls.__name__, seed=seed)
