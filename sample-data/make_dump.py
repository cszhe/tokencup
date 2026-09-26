"""Regenerate the sample database dump from a live TokenCup database.

    .venv/bin/python sample-data/make_dump.py --verify

Writes sample-data/tokencup_sample.sql: the schema from backend/db.py plus
every finished game and its moves, in a stable format so each new snapshot
is an append-only diff against the last one. Before writing, the games
already in the dump are re-rendered from the database and compared with the
file byte-for-byte; if a released game changed or disappeared the script
refuses to write (override with --allow-changes). It also updates the
game/move counts quoted in README.md and sample-data/README.md.

The full release procedure (commit, tag, GitHub release) is in AGENTS.md.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import os
import re
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from config import load_config  # noqa: E402
from db import SCHEMA_STATEMENTS, Database  # noqa: E402

DUMP_PATH = REPO / "sample-data" / "tokencup_sample.sql"
README_PATHS = [REPO / "README.md", REPO / "sample-data" / "README.md"]

HEADER = (
    "-- TokenCup sample data dump\n"
    "-- Generated for the open-source release. Contains no personal data --\n"
    "-- just AI agent names and recorded chess games.\n"
)

GAME_COLUMNS = [
    "id", "match_id", "white_name", "black_name", "status", "result",
    "termination", "fen", "pgn", "created_at", "updated_at",
]
MOVE_COLUMNS = ["id", "game_id", "ply", "side", "san", "uci", "fen_after", "created_at"]

# Game rows start with the quoted UUID; move rows start with a numeric id.
GAME_ROW_RE = re.compile(r"^\('([0-9a-f-]{36})', ", re.M)
# "20 games, 1,681 moves" -- README.md wraps the line between the two words.
COUNTS_RE = re.compile(r"\b\d+(\s+)games, [\d,]+ moves")


def sql_literal(value) -> str:
    """Render one value as a MariaDB literal. Newlines stay literal (PGNs span lines)."""
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime.datetime):
        return f"'{value:%Y-%m-%d %H:%M:%S}'"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _insert(table: str, columns: list[str], rows: list[dict]) -> str:
    if not rows:
        return ""
    values = ",\n".join(
        "(" + ", ".join(sql_literal(row[col]) for col in columns) + ")" for row in rows
    )
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values};\n"


def render_dump(games: list[dict], moves: list[dict]) -> str:
    """Render the whole dump. Games are ordered by creation time, moves by (game_id, ply)."""
    games = sorted(games, key=lambda g: (g["created_at"], g["id"]))
    moves = sorted(moves, key=lambda m: (m["game_id"], m["ply"]))
    parts = [HEADER]
    for statement in SCHEMA_STATEMENTS:
        parts.append("\n" + textwrap.dedent(statement).strip() + ";\n")
    parts.append("\n\n")
    parts.append(_insert("games", GAME_COLUMNS, games))
    parts.append("\n")
    parts.append(_insert("moves", MOVE_COLUMNS, moves))
    return "".join(parts)


def check_released(current: str, games: list[dict], moves: list[dict]) -> list[str]:
    """Describe any game already in `current` that no longer matches the database.

    Returns an empty list when the released games re-render byte-for-byte.
    """
    released = GAME_ROW_RE.findall(current)
    finished = {g["id"] for g in games}
    problems = [
        f"released game {gid} is no longer a finished game in the database"
        for gid in released
        if gid not in finished
    ]
    if problems:
        return problems

    ids = set(released)
    expected = render_dump(
        [g for g in games if g["id"] in ids], [m for m in moves if m["game_id"] in ids]
    )
    if expected != current:
        diff = difflib.unified_diff(
            current.splitlines(), expected.splitlines(),
            "committed dump", "database", lineterm="", n=0,
        )
        problems.append(
            "released games differ from the database:\n" + "\n".join(list(diff)[:20])
        )
    return problems


def replace_counts(text: str, n_games: int, n_moves: int) -> tuple[str, int]:
    """Rewrite every "N games, N moves" in `text`; returns (new text, replacements)."""
    return COUNTS_RE.subn(
        lambda m: f"{n_games}{m.group(1)}games, {n_moves:,} moves", text
    )


def fetch_games(config) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (finished games, their moves, unfinished games)."""
    with Database(config).transaction() as cur:
        cur.execute(f"SELECT {', '.join(GAME_COLUMNS)} FROM games")
        games = list(cur.fetchall())
        cur.execute(f"SELECT {', '.join(MOVE_COLUMNS)} FROM moves")
        moves = list(cur.fetchall())
    finished = [g for g in games if g["status"] == "finished"]
    ids = {g["id"] for g in finished}
    unfinished = [g for g in games if g["status"] != "finished"]
    return finished, [m for m in moves if m["game_id"] in ids], unfinished


def verify_in_docker(path: Path, n_games: int, n_moves: int, image: str = "mariadb:lts") -> None:
    """Load `path` into a throwaway MariaDB container and check the row counts."""
    name = f"tokencup-dump-verify-{os.getpid()}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "MARIADB_ROOT_PASSWORD=verify", "-e", "MARIADB_DATABASE=tokencup", image],
        check=True, stdout=subprocess.DEVNULL,
    )
    # TCP rather than the socket: the image's init-time server skips networking,
    # so a TCP connection only succeeds once the real server is up.
    client = ["docker", "exec", "-i", name, "mariadb", "--protocol=tcp",
              "-h127.0.0.1", "-uroot", "-pverify"]
    try:
        for _ in range(90):
            if subprocess.run(client + ["-e", "SELECT 1"], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("MariaDB container did not become ready")

        with path.open("rb") as fh:
            subprocess.run(client + ["tokencup"], stdin=fh, check=True)
        out = subprocess.run(
            client + ["-N", "tokencup", "-e",
                      "SELECT (SELECT COUNT(*) FROM games), (SELECT COUNT(*) FROM moves), "
                      "(SELECT COUNT(*) FROM moves m LEFT JOIN games g ON g.id = m.game_id "
                      "WHERE g.id IS NULL)"],
            check=True, capture_output=True, text=True,
        ).stdout
        got = tuple(int(n) for n in out.split())
        if got != (n_games, n_moves, 0):
            raise RuntimeError(
                f"loaded (games, moves, orphan moves) = {got}, expected {(n_games, n_moves, 0)}"
            )
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="config file (default: $TOKENCUP_CONFIG or backend/config.toml)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--allow-changes", action="store_true",
                        help="write even if games already in the dump changed or disappeared")
    parser.add_argument("--verify", action="store_true",
                        help="after writing, load the dump into a throwaway MariaDB container "
                             "(needs Docker) and check the row counts")
    args = parser.parse_args(argv)

    games, moves, unfinished = fetch_games(load_config(args.config))
    for g in unfinished:
        print(f"skipping unfinished game {g['id']} "
              f"({g['white_name']} v {g['black_name']}, status {g['status']})")
    if not games:
        print("no finished games in the database", file=sys.stderr)
        return 1

    current = DUMP_PATH.read_text(encoding="utf-8") if DUMP_PATH.exists() else ""
    problems = check_released(current, games, moves) if current else []
    for problem in problems:
        print(f"WARNING: {problem}", file=sys.stderr)
    if problems and not args.allow_changes:
        print("refusing to write; rerun with --allow-changes if this is intended", file=sys.stderr)
        return 1

    released = set(GAME_ROW_RE.findall(current))
    new_games = sorted(
        (g for g in games if g["id"] not in released), key=lambda g: (g["created_at"], g["id"])
    )
    plies = Counter(m["game_id"] for m in moves)
    print(f"{len(games)} games, {len(moves):,} moves ({len(new_games)} new)")
    if new_games:
        print("\nNew games (Markdown for the release notes):\n")
        print("| White | Black | Result | Termination | Plies |")
        print("|---|---|---|---|---|")
        for g in new_games:
            print(f"| {g['white_name']} | {g['black_name']} | {g['result']} "
                  f"| {g['termination']} | {plies[g['id']]} |")
    else:
        print("nothing new to release")

    if args.dry_run:
        return 0

    DUMP_PATH.write_text(render_dump(games, moves), encoding="utf-8")
    print(f"\nwrote {DUMP_PATH.relative_to(REPO)}")
    for path in README_PATHS:
        text, found = replace_counts(path.read_text(encoding="utf-8"), len(games), len(moves))
        if found:
            path.write_text(text, encoding="utf-8")
        else:
            print(f"WARNING: no 'N games, N moves' count in {path.relative_to(REPO)}; "
                  f"update it by hand", file=sys.stderr)

    if args.verify:
        verify_in_docker(DUMP_PATH, len(games), len(moves))
        print(f"verified: loads into a fresh MariaDB as {len(games)} games, "
              f"{len(moves):,} moves, no orphan moves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
