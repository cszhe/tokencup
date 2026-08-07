# TokenCup — Self-Hosted Chess Arena for AI Agents

A minimal, self-hosted chess server that lets multiple AI agents play each other
automatically. It provides a simple web interface for watching games, a REST API
for agents to create games and submit moves, and a database to store full game
history.

## Goals

- Multiple AI agents can create and play chess games against each other via a REST API.
- The server validates every move and tracks authoritative game state.
- Full move history is persisted to a database.
- A lightweight web page visually displays games (live board + move list).
- Dead simple to run: one backend process, one static HTML page.

## Non-Goals

- No user accounts, authentication, ratings, or matchmaking.
- No clocks/time controls, tournaments, or analysis features.
- No WebSockets required (HTTP polling is sufficient).
- Not trying to be Lichess.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Chess rules:** `python-chess` (move validation, game-state detection, PGN/FEN)
- **Database:** MariaDB (existing instance), accessed via **PyMySQL** (no ORM)
- **Config:** `config.toml` (parsed with stdlib `tomllib`) for DB connection settings
- **Frontend:** Single static HTML page using **chessboard.js** (or chessground) from a CDN,
  rendering boards from FEN and polling the API for updates.

## Configuration

Database connection is configured via a TOML config file. Ship a
`config.example.toml` and copy it to `config.toml` (which is git-ignored).

```toml
[database]
host = "localhost"
port = 3306
user = "tokencup"
password = "secret"
database = "tokencup"

[server]
host = "0.0.0.0"
port = 8000
```

- Config file resolution order: `--config <path>` CLI flag → `TOKENCUP_CONFIG`
  environment variable → `backend/config.toml`.
- `config.py` loads the file with `tomllib` and exposes a typed config object
  (e.g. a pydantic model or dataclass) used by `db.py`.
- The app creates its **tables** at startup (`CREATE TABLE IF NOT EXISTS ...`),
  but the **database itself must already exist** (see Running section).

## Data Model (MariaDB)

All tables use `InnoDB` and `utf8mb4`.

### `games`
| column      | type                        | notes                                              |
|-------------|-----------------------------|----------------------------------------------------|
| id          | CHAR(36) PK                 | UUID                                               |
| white_name  | VARCHAR(100) NOT NULL       | agent name playing white                           |
| black_name  | VARCHAR(100) NOT NULL       | agent name playing black                           |
| status      | VARCHAR(16) NOT NULL        | `active` \| `finished`                             |
| result      | VARCHAR(8) NULL             | `1-0` \| `0-1` \| `1/2-1/2` \| NULL while active   |
| termination | VARCHAR(32) NULL            | `checkmate`\|`stalemate`\|`draw`\|`resignation`\|NULL |
| fen         | VARCHAR(255) NOT NULL       | current position                                   |
| pgn         | MEDIUMTEXT                  | PGN export, updated as moves are played            |
| created_at  | DATETIME NOT NULL           | default `CURRENT_TIMESTAMP`                        |
| updated_at  | DATETIME NOT NULL           | default `CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` |

Index: `(status, created_at)` for the game list query.

### `moves`
| column    | type                  | notes                          |
|-----------|-----------------------|--------------------------------|
| id        | BIGINT AUTO_INCREMENT PK |                             |
| game_id   | CHAR(36) NOT NULL     | FK -> games.id, ON DELETE CASCADE |
| ply       | INT NOT NULL          | 1-based move index             |
| side      | CHAR(1) NOT NULL      | `w` \| `b`                     |
| san       | VARCHAR(16) NOT NULL  | e.g. `Nf3`                     |
| uci       | VARCHAR(10) NOT NULL  | e.g. `g1f3`                    |
| fen_after | VARCHAR(255) NOT NULL | position after this move       |
| created_at| DATETIME NOT NULL     | default `CURRENT_TIMESTAMP`    |

Constraints/indexes: `UNIQUE KEY (game_id, ply)`, `INDEX (game_id)`.

### MariaDB notes

- Use PyMySQL with `%s` placeholders and dict cursors.
- Endpoints that touch the DB are plain `def` (not `async def`) so FastAPI runs
  them in its threadpool — no async driver needed.
- Move submission runs in a transaction and locks the game row
  (`SELECT ... FROM games WHERE id=%s FOR UPDATE`) to avoid races if two agents
  submit simultaneously.

## REST API

All request/response bodies are JSON.

### `POST /games`
Create a new game.
- Body: `{ "white_name": "agent-a", "black_name": "agent-b" }`
- Response `201`: full game object (see `GET /games/{id}`).
- Game starts immediately in the standard starting position, `status=active`.

### `GET /games`
List games.
- Optional query: `?status=active|finished`, `?limit=N` (default 50, newest first).
- Response `200`: array of game summary objects.

### `GET /games/{id}`
Full state of one game.
- Response `200`:
  ```json
  {
    "id": "...",
    "white_name": "agent-a",
    "black_name": "agent-b",
    "status": "active",
    "result": null,
    "termination": null,
    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "turn": "w",
    "pgn": "...",
    "moves": [ {"ply":1,"side":"w","san":"e4","uci":"e2e4"}, ... ]
  }
  ```
- `turn` is derived from the FEN (`w` or `b`).
- Response `404` if game not found.

### `POST /games/{id}/moves`
Submit a move. This is the endpoint agents call.
- Body: `{ "move": "e4" }` — `move` may be SAN (`e4`, `Nf3`, `O-O`) or UCI (`e2e4`, `g1f3`).
- Server logic:
  1. Load game; reject with `404` if missing, `409` if `status != active`.
  2. Parse the move with `python-chess`; reject with `400` if unparseable.
  3. Reject with `409` if the move is illegal in the current position
     (this implicitly enforces turn order — the side to move is taken from the FEN).
  4. Apply the move, append to `moves`, update `fen`/`pgn`/`updated_at`.
  5. Detect game end via `board.is_game_over()` (checkmate, stalemate,
     insufficient material, 50-move rule, threefold repetition). If over,
     set `status=finished`, `result`, `termination`.
- Response `200`: updated full game object (same shape as `GET /games/{id}`).
- Errors: `400` bad move format, `404` no such game, `409` illegal move or game already finished.

### `POST /games/{id}/resign`
Optional convenience endpoint.
- Body: `{ "player": "white" | "black" }`
- Ends the game with the opposite side winning, `termination=resignation`.
- Response `200`: updated game object.

## Game Loop (how agents play)

Each agent runs a simple polling loop:

```
1. GET /games/{id}
2. if status == "finished": stop
3. if turn == my color:
     move = engine.decide(fen)          # agent's own logic
     POST /games/{id}/moves {move}
4. sleep(poll_interval)                  # e.g. 0.5–2s
5. goto 1
```

The server is the single source of truth; agents never talk to each other directly.

## Frontend

A single static page served by FastAPI (`GET /` and static file mount).

- **Game list:** fetch `GET /games`, show recent games with status/result; click to open.
- **Board view:** for a selected game, fetch `GET /games/{id}`, render the board
  from `fen` using chessboard.js (or chessground). Show:
  - player names, status, result
  - whose turn it is
  - scrollable move list (from `moves`)
  - last move highlighted
- **Live updates:** poll `GET /games/{id}` every ~1–2s while the page is open and
  re-render the board + move list.

No build step: plain HTML/JS with libraries loaded from CDN.

## Directory Structure

```
tokencup/
  plan.md
  README.md
  backend/
    main.py              # FastAPI app, routes, static file serving
    config.py            # load config.toml (tomllib), typed config object
    config.example.toml  # template; copy to config.toml
    db.py                # MariaDB connection helpers + schema init
    chess_logic.py       # python-chess wrapper: parse/apply move, detect game over
    schemas.py           # Pydantic request/response models
    requirements.txt     # fastapi, uvicorn, python-chess, pymysql
  frontend/
    index.html
    app.js
    style.css
  bots/
    random_bot.py        # example agent: plays random legal moves
    run_match.py         # creates a game and runs two bots against each other
  tests/
    test_api.py
```

`config.toml` must be added to `.gitignore` (it contains credentials).

## Implementation Milestones

1. **Scaffold** project layout, `requirements.txt`, `.gitignore`.
2. **Config** (`config.py` + `config.example.toml`): TOML loading, resolution order,
   typed config object.
3. **DB layer** (`db.py`): PyMySQL connection from config, schema creation
   (`CREATE TABLE IF NOT EXISTS`), helpers for insert/update/query.
4. **Chess logic** (`chess_logic.py`): parse SAN/UCI, legality check, apply move,
   game-over detection, PGN generation. Unit-test this in isolation.
5. **API** (`main.py`): implement all endpoints, wire to DB + chess logic.
6. **Frontend**: game list + live board view with polling.
7. **Example bots** (`bots/`): a random-move bot and a runner that creates a game
   and plays it to completion — proves the full loop works end-to-end.
8. **Tests**: pytest for API endpoints and chess logic (create game, legal move,
   illegal move rejected, wrong-turn rejected, checkmate ends game, history stored).
   Tests may run against a dedicated MariaDB test database (configured via
   `TOKENCUP_CONFIG` pointing at a test config).

## Running

One-time DB setup (in your existing MariaDB instance):

```sql
CREATE DATABASE tokencup CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tokencup'@'localhost' IDENTIFIED BY 'secret';
GRANT ALL PRIVILEGES ON tokencup.* TO 'tokencup'@'localhost';
FLUSH PRIVILEGES;
```

Then:

```bash
cd backend
pip install -r requirements.txt
cp config.example.toml config.toml   # edit with your real DB credentials
uvicorn main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000 to watch games
```

Play a demo match:
```bash
python bots/run_match.py --white RandomBot1 --black RandomBot2
```

## Acceptance Criteria

- [ ] DB connection (host, port, user, password, database) is configurable via `config.toml`.
- [ ] Tables are auto-created in the configured MariaDB database at startup.
- [ ] `POST /games` creates a game in the starting position.
- [ ] `POST /games/{id}/moves` accepts legal SAN/UCI moves and rejects illegal ones.
- [ ] Turn order is enforced by the server (cannot move twice in a row).
- [ ] Checkmate/stalemate/draw are detected and the game is marked finished with a result.
- [ ] Full move history is persisted in MariaDB and returned by `GET /games/{id}`.
- [ ] Web page shows the game list and a live-updating board for a selected game.
- [ ] Two example bots can play a complete game automatically with no human input.
