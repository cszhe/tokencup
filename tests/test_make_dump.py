"""Tests for sample-data/make_dump.py -- pure, no database."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sample-data"))

import make_dump as md  # noqa: E402

GAME_A = "aaaaaaaa-0000-0000-0000-000000000000"
GAME_B = "bbbbbbbb-0000-0000-0000-000000000000"


def game(gid: str, created: datetime, result: str = "1-0") -> dict:
    return {
        "id": gid, "match_id": None, "white_name": "White", "black_name": "Black",
        "status": "finished", "result": result, "termination": "checkmate",
        "fen": "8/8/8/8/8/8/8/8 w - - 0 1", "pgn": '[Event "TokenCup"]\n\n1. e4 1-0',
        "created_at": created, "updated_at": created,
    }


def move(mid: int, gid: str, ply: int) -> dict:
    return {
        "id": mid, "game_id": gid, "ply": ply, "side": "w" if ply % 2 else "b",
        "san": "e4", "uci": "e2e4", "fen_after": "8/8/8/8/8/8/8/8 b - - 0 1",
        "created_at": datetime(2026, 1, 1, 12, 0, ply),
    }


def test_sql_literal():
    assert md.sql_literal(None) == "NULL"
    assert md.sql_literal(42) == "42"
    assert md.sql_literal(datetime(2026, 9, 26, 8, 29, 8)) == "'2026-09-26 08:29:08'"
    assert md.sql_literal("it's a \\ test") == "'it\\'s a \\\\ test'"
    assert md.sql_literal("line1\nline2") == "'line1\nline2'"


def test_render_orders_games_by_creation_and_moves_by_game_then_ply():
    # B is created first but sorts after A by id; move ids are deliberately scrambled.
    games = [game(GAME_A, datetime(2026, 2, 1)), game(GAME_B, datetime(2026, 1, 1))]
    moves = [move(9, GAME_B, 1), move(3, GAME_A, 2), move(7, GAME_A, 1)]
    dump = md.render_dump(games, moves)

    assert md.GAME_ROW_RE.findall(dump) == [GAME_B, GAME_A]
    assert dump.index("(7, ") < dump.index("(3, ") < dump.index("(9, ")
    assert dump.endswith("'2026-01-01 12:00:01');\n")


def test_check_released_accepts_appended_games():
    a = game(GAME_A, datetime(2026, 1, 1))
    current = md.render_dump([a], [move(1, GAME_A, 1)])

    b = game(GAME_B, datetime(2026, 2, 1))
    assert md.check_released(current, [a, b], [move(1, GAME_A, 1), move(2, GAME_B, 1)]) == []


def test_check_released_flags_changed_or_missing_games():
    current = md.render_dump([game(GAME_A, datetime(2026, 1, 1))], [move(1, GAME_A, 1)])

    changed = md.check_released(
        current, [game(GAME_A, datetime(2026, 1, 1), result="0-1")], [move(1, GAME_A, 1)]
    )
    assert len(changed) == 1 and "differ" in changed[0]

    missing = md.check_released(current, [game(GAME_B, datetime(2026, 1, 1))], [])
    assert missing == [f"released game {GAME_A} is no longer a finished game in the database"]


def test_replace_counts_handles_wrapped_lines():
    text = "played games (18\ngames, 1,466 moves) if you want"
    assert md.replace_counts(text, 20, 1681) == (
        "played games (20\ngames, 1,681 moves) if you want", 1
    )
