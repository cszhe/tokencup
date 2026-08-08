"""Request and response models for the TokenCup API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreateGameRequest(BaseModel):
    white_name: str = Field(min_length=1, max_length=100)
    black_name: str = Field(min_length=1, max_length=100)
    match_id: str | None = Field(default=None, max_length=36)


class MoveRequest(BaseModel):
    move: str = Field(min_length=1, max_length=16, description="SAN or UCI notation")


class ResignRequest(BaseModel):
    player: Literal["white", "black"]
    termination: Literal["resignation", "forfeit"] = "resignation"


class AdjudicateRequest(BaseModel):
    """End a game with an outcome the rules did not produce on their own.

    Used by the judge for outcomes resignation cannot express -- notably a
    drawn game that hit the ply limit.
    """

    result: Literal["1-0", "0-1", "1/2-1/2"]
    termination: str = Field(default="adjudicated", max_length=32)


class MoveOut(BaseModel):
    ply: int
    side: str
    san: str
    uci: str
    fen_after: str


class GameSummary(BaseModel):
    """Shape returned by the game list -- no move array, no PGN."""

    id: str
    match_id: str | None = None
    white_name: str
    black_name: str
    status: str
    result: str | None = None
    termination: str | None = None
    fen: str
    turn: str
    move_count: int = 0
    created_at: datetime
    updated_at: datetime


class GameDetail(GameSummary):
    """Full game state, returned by create / get / move / resign."""

    pgn: str | None = None
    moves: list[MoveOut] = []


class LeaderboardEntry(BaseModel):
    agent_name: str
    games: int
    wins: int
    draws: int
    losses: int
    win_rate: float
