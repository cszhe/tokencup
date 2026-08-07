"""MariaDB access layer: connections, schema init, and game/move queries.

Uses PyMySQL directly (no ORM) with `%s` placeholders and dict cursors. All
callers are synchronous -- FastAPI runs plain `def` endpoints in a threadpool,
so no async driver is needed.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

from config import Config

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS games (
        id          CHAR(36)     NOT NULL PRIMARY KEY,
        match_id    CHAR(36)     NULL,
        white_name  VARCHAR(100) NOT NULL,
        black_name  VARCHAR(100) NOT NULL,
        status      VARCHAR(16)  NOT NULL,
        result      VARCHAR(8)   NULL,
        termination VARCHAR(32)  NULL,
        fen         VARCHAR(255) NOT NULL,
        pgn         MEDIUMTEXT   NULL,
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_games_status_created (status, created_at),
        INDEX idx_games_match (match_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS moves (
        id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
        game_id    CHAR(36)     NOT NULL,
        ply        INT          NOT NULL,
        side       CHAR(1)      NOT NULL,
        san        VARCHAR(16)  NOT NULL,
        uci        VARCHAR(10)  NOT NULL,
        fen_after  VARCHAR(255) NOT NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_moves_game_ply (game_id, ply),
        INDEX idx_moves_game (game_id),
        CONSTRAINT fk_moves_game FOREIGN KEY (game_id)
            REFERENCES games (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


class Database:
    """Owns the connection settings and hands out connections."""

    def __init__(self, config: Config):
        self._settings = dict(
            host=config.database.host,
            port=config.database.port,
            user=config.database.user,
            password=config.database.password,
            database=config.database.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(**self._settings)

    @contextmanager
    def transaction(self) -> Iterator[pymysql.cursors.DictCursor]:
        """Run a block in a transaction, committing on success.

        A new connection per request keeps things simple and avoids sharing
        connection state across FastAPI's threadpool workers.
        """
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.transaction() as cur:
            for statement in SCHEMA_STATEMENTS:
                cur.execute(statement)

    def ping(self) -> None:
        """Raise if the database is unreachable."""
        conn = self.connect()
        try:
            conn.ping(reconnect=False)
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Queries. Each takes a cursor so callers control transaction boundaries.
# --------------------------------------------------------------------------

GAME_COLUMNS = (
    "id, match_id, white_name, black_name, status, result, termination, "
    "fen, pgn, created_at, updated_at"
)


def create_game(
    cur, white_name: str, black_name: str, fen: str, pgn: str, match_id: str | None = None
) -> str:
    game_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO games (id, match_id, white_name, black_name, status, fen, pgn)
        VALUES (%s, %s, %s, %s, 'active', %s, %s)
        """,
        (game_id, match_id, white_name, black_name, fen, pgn),
    )
    return game_id


def get_game(cur, game_id: str, for_update: bool = False) -> dict[str, Any] | None:
    """Fetch one game. `for_update` locks the row for the rest of the transaction."""
    sql = f"SELECT {GAME_COLUMNS} FROM games WHERE id = %s"
    if for_update:
        sql += " FOR UPDATE"
    cur.execute(sql, (game_id,))
    return cur.fetchone()


def list_games(cur, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = f"SELECT {GAME_COLUMNS} FROM games"
    params: list[Any] = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    return list(cur.fetchall())


def get_moves(cur, game_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT ply, side, san, uci, fen_after
        FROM moves WHERE game_id = %s ORDER BY ply ASC
        """,
        (game_id,),
    )
    return list(cur.fetchall())


def count_moves_for(cur, game_ids: list[str]) -> dict[str, int]:
    """Move counts for several games in one query (avoids an N+1 in the list view)."""
    if not game_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(game_ids))
    cur.execute(
        f"SELECT game_id, COUNT(*) AS n FROM moves "
        f"WHERE game_id IN ({placeholders}) GROUP BY game_id",
        game_ids,
    )
    counts = {row["game_id"]: row["n"] for row in cur.fetchall()}
    return {gid: counts.get(gid, 0) for gid in game_ids}


def get_uci_moves(cur, game_id: str) -> list[str]:
    """The authoritative move list used to rebuild the board."""
    cur.execute("SELECT uci FROM moves WHERE game_id = %s ORDER BY ply ASC", (game_id,))
    return [row["uci"] for row in cur.fetchall()]


def insert_move(
    cur, game_id: str, ply: int, side: str, san: str, uci: str, fen_after: str
) -> None:
    cur.execute(
        """
        INSERT INTO moves (game_id, ply, side, san, uci, fen_after)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (game_id, ply, side, san, uci, fen_after),
    )


def update_game_state(
    cur,
    game_id: str,
    fen: str,
    pgn: str,
    status: str,
    result: str | None,
    termination: str | None,
) -> None:
    cur.execute(
        """
        UPDATE games
        SET fen = %s, pgn = %s, status = %s, result = %s, termination = %s
        WHERE id = %s
        """,
        (fen, pgn, status, result, termination, game_id),
    )
