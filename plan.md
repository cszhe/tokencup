# TokenCup — Self-Hosted Chess Arena for AI Agents

A minimal, self-hosted chess server that lets multiple AI agents play each other
automatically. It provides a simple web interface for watching games, a REST API
for creating games and submitting moves, and a database to store full game
history.

Games are run by a trusted **judge agent**, which is the only client that talks to
the server. The judge asks each player agent for a move and submits it. Player
agents never touch the API. This keeps the server simple: it validates chess rules
and stores history, while all policy (per-move timeouts, illegal-move retries,
forfeits) lives in the judge.

## Goals

- Multiple AI agents can play chess games against each other, orchestrated by a judge.
- The server validates every move and tracks authoritative game state.
- Full move history is persisted to a database.
- A lightweight web page visually displays games (live board + move list).
- Dead simple to run: one backend process, one static HTML page.

## Non-Goals

- No user accounts, authentication, ratings, or matchmaking. The judge is trusted;
  there are no per-player tokens and the server does not check who is submitting.
- No server-side handling of misbehaving agents (illegal-move retries, unresponsive
  players, forfeits). The server rejects illegal moves with an error; deciding what
  to do about it is the judge's job.
- No clocks/time controls, tournaments, or analysis features. Tournaments are out of
  scope for this build, but the schema is designed so they can be added without a
  migration (see `match_id`).
- No WebSockets required (HTTP polling is sufficient).
- Not trying to be Lichess.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Chess rules:** `python-chess` (move validation, game-state detection, PGN/FEN)
- **Database:** MariaDB (existing instance), accessed via **PyMySQL** (no ORM)
- **Config:** `config.toml` (parsed with stdlib `tomllib`) for DB connection settings
- **Frontend:** Single static HTML page using **chessground** (the board Lichess uses),
  loaded as an ES module from a CDN via an import map — no build step, no jQuery.
  (`chessboard.js` was considered and rejected: unmaintained and requires jQuery.)
  Renders boards from FEN and polls the API for updates.

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

> **Key invariant: the `moves` table is the authoritative game state, not `fen`.**
> `games.fen` and `games.pgn` are denormalized convenience columns for the frontend
> and the judge. Any rules decision (legality, game over) is made on a board rebuilt
> by replaying `moves`. See "Chess logic" below for why this matters.

### `games`
| column      | type                        | notes                                              |
|-------------|-----------------------------|----------------------------------------------------|
| id          | CHAR(36) PK                 | UUID                                               |
| match_id    | CHAR(36) NULL               | reserved for future tournaments; NULL for one-off games |
| white_name  | VARCHAR(100) NOT NULL       | agent identifier playing white (stable, not a display string) |
| black_name  | VARCHAR(100) NOT NULL       | agent identifier playing black                     |
| status      | VARCHAR(16) NOT NULL        | `active` \| `finished`                             |
| result      | VARCHAR(8) NULL             | `1-0` \| `0-1` \| `1/2-1/2` \| NULL while active   |
| termination | VARCHAR(32) NULL            | `checkmate`\|`stalemate`\|`draw`\|`resignation`\|`forfeit`\|NULL |
| fen         | VARCHAR(255) NOT NULL       | current position (derived; see invariant above)    |
| pgn         | MEDIUMTEXT                  | PGN export, updated as moves are played (derived)  |
| created_at  | DATETIME NOT NULL           | default `CURRENT_TIMESTAMP`                        |
| updated_at  | DATETIME NOT NULL           | default `CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` |

Indexes: `(status, created_at)` for the game list query, `(match_id)` for future
tournament queries.

`white_name` / `black_name` are stable agent identifiers so future standings can
group by them without a schema change.

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
  (`SELECT ... FROM games WHERE id=%s FOR UPDATE`) to avoid races if two requests
  arrive simultaneously. The `moves` read, legality check, and insert all happen
  inside that transaction.
- The move endpoint reads the game's full `uci` move list (ordered by `ply`) and
  rebuilds the board from it — it does **not** trust `games.fen`.

## Chess logic

`chess_logic.py` centres on one function:

```python
def build_board(uci_moves: list[str]) -> chess.Board:
    board = chess.Board()          # standard starting position
    for uci in uci_moves:
        board.push_uci(uci)
    return board
```

**This replay is mandatory, not an optimization choice.** A `chess.Board`
constructed from a FEN has an empty move stack, so:

- `board.is_repetition()` / threefold detection cannot see prior positions and
  silently never fires;
- `board.is_game_over()` defaults to `claim_draw=False`, so it does not report
  threefold repetition or the 50-move rule even with a full stack.

So game-over detection must use a replayed board and pass `claim_draw=True`:
`board.is_game_over(claim_draw=True)` and `board.result(claim_draw=True)`.
Replaying a few hundred moves is negligible per request.

Ordering note: SAN must be generated **before** the move is pushed
(`san = board.san(move)`, then `board.push(move)`) — a common ordering bug.

Both the move endpoint and PGN generation go through `build_board`.

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
Submit a move. This is the endpoint the judge calls.
- Body: `{ "move": "e4" }` — `move` may be SAN (`e4`, `Nf3`, `O-O`) or UCI (`e2e4`, `g1f3`).
- Server logic (all inside one transaction, with the game row locked `FOR UPDATE`):
  1. Load game; reject with `404` if missing, `409` if `status != active`.
  2. Load the game's `uci` moves ordered by `ply` and `build_board(...)` from them.
  3. Parse the move with `python-chess` against that board; reject with `400` if
     unparseable.
  4. Reject with `409` if the move is illegal in the current position
     (this implicitly enforces turn order — the side to move comes from the replayed
     board, so a player cannot move twice in a row).
  5. Compute SAN, push the move, append the row to `moves`, update
     `fen`/`pgn`/`updated_at`.
  6. Detect game end via `board.is_game_over(claim_draw=True)` (checkmate, stalemate,
     insufficient material, 50-move rule, threefold repetition). If over, set
     `status=finished`, `result` (`board.result(claim_draw=True)`), and `termination`.
- Response `200`: updated full game object (same shape as `GET /games/{id}`).
- Errors: `400` bad move format, `404` no such game, `409` illegal move or game already finished.
- Error responses carry a human-readable `detail` string; the judge feeds this back to
  the player agent on a retry.

### `POST /games/{id}/resign`
Ends a game early. **Required** — this is how the judge adjudicates forfeits, not just
a convenience for voluntary resignation.
- Body: `{ "player": "white" | "black", "termination": "resignation" | "forfeit" }`
  (`termination` defaults to `resignation`).
- Ends the game with the opposite side winning, `status=finished`, `result` set
  accordingly.
- Response `200`: updated game object. `409` if the game is already finished.

## Game Loop (how the judge runs a game)

One judge process orchestrates a whole game. Only the judge calls the API.

```
game = POST /games {white_name, black_name}
loop:
  state = GET /games/{id}
  if state.status == "finished": stop
  player = white_agent if state.turn == "w" else black_agent
  move = player.get_move(state)              # judge enforces its own timeout here
  for attempt in 1..MAX_RETRIES:
    r = POST /games/{id}/moves {move}
    if r.ok: break
    move = player.get_move(state, rejected=move, reason=r.detail)
  else:
    POST /games/{id}/resign {player: <that side>, termination: "forfeit"}
```

Policy lives entirely in the judge:
- per-move timeout for a slow or hung player agent;
- illegal-move retry budget (`MAX_RETRIES`, default 3), feeding the server's rejection
  `detail` back to the agent so it can correct itself;
- forfeit on exhausting retries or on timeout, via the resign endpoint.

The server stays the single source of truth for chess rules; player agents never talk
to it, or to each other.

### Player agent interface

Player agents are in-process Python objects behind a small interface, so the judge can
mix bot types freely:

```python
class PlayerAgent:
    name: str
    def get_move(self, state, rejected=None, reason=None) -> str: ...
```

`state` carries at least the FEN, the move history, and the list of legal moves.
`rejected`/`reason` are set on a retry after the server refused a move. `RandomBot` is
the reference implementation. An HTTP- or subprocess-backed player can implement the
same interface later without any server change.

## Frontend

A single static page served by FastAPI (`GET /` and static file mount).

- **Game list:** fetch `GET /games`, show recent games with status/result; click to open.
- **Board view:** for a selected game, fetch `GET /games/{id}`, render the board
  from `fen` using chessground. Show:
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
  judge/
    judge.py             # orchestrates a game: asks players, submits moves, forfeits
    players.py           # PlayerAgent interface + RandomBot
    run_match.py         # CLI: creates a game and runs two players to completion
  tests/
    test_chess_logic.py  # pure, no DB
    test_api.py          # requires a MariaDB test database
```

`config.toml` must be added to `.gitignore` (it contains credentials).

## Implementation Milestones

All milestones are complete; 60 tests pass.

1. [x] **Scaffold** project layout, `requirements.txt`, `.gitignore`.
2. [x] **Config** (`config.py` + `config.example.toml`): TOML loading, resolution order,
   typed config object. Validates types and sections so a typo fails at startup with a
   clear message rather than deep inside PyMySQL.
3. [x] **DB layer** (`db.py`): PyMySQL connection from config, schema creation
   (`CREATE TABLE IF NOT EXISTS`), helpers for insert/update/query.
4. [x] **Chess logic** (`chess_logic.py`): `build_board` replay, parse SAN/UCI, legality
   check, apply move, game-over detection with `claim_draw=True`, PGN generation.
   Unit-tested in isolation — no DB.
5. [x] **API** (`main.py`): all endpoints, wired to DB + chess logic.
6. [x] **Frontend**: game list + live board view with polling (chessground 9.2.1 via
   CDN, no build step). Not yet eyeballed in a browser — see Acceptance Criteria.
7. [x] **Judge + players** (`judge/`): the `PlayerAgent` interface, `RandomBot` /
   `GreedyBot` / `AlwaysIllegalBot`, the judge loop with timeout/retry/forfeit policy,
   and `run_match.py` — proves the full loop works end-to-end.
8. [x] **Tests**: pytest for chess logic and API endpoints (create game, legal move,
   illegal move rejected, wrong-turn rejected, checkmate ends game, threefold and
   50-move draws detected, history stored). `test_chess_logic.py` is pure and always
   runnable; `test_api.py` needs a reachable MariaDB (selected via `TOKENCUP_CONFIG`,
   skipping if absent) and deletes the games it creates.

### Deviations from this plan, found while building

1. **`0000` is playable as SAN.** `board.parse_san("0000")` returns a *null move* and
   passes a legality check, so an agent could have skipped its turn. Now rejected
   explicitly in `parse_move`, with a regression test.
2. **Added `POST /games/{id}/adjudicate`.** The judge's ply-limit backstop must end a
   game in a draw, which `resign` cannot express — without it the judge pinned a loss
   on whoever happened to be on move.
3. **`GET /games` move counts** use one `GROUP BY` query rather than one per row.
4. **Claim-draw timing.** Threefold repetition and the fifty-move rule become claimable
   when the side to move can *reach* the threshold, so both fire one ply earlier than
   naively expected. Test fixtures were corrected to match; the server was right.

## Running

One-time DB setup (in your existing MariaDB instance):

```sql
CREATE DATABASE tokencup CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- Use '%' (not 'localhost') when the app connects to MariaDB over the network.
CREATE USER 'tokencup'@'%' IDENTIFIED BY 'secret';
GRANT ALL PRIVILEGES ON tokencup.* TO 'tokencup'@'%';
FLUSH PRIVILEGES;
```

Then:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp backend/config.example.toml backend/config.toml   # edit with real DB credentials
.venv/bin/python backend/main.py                     # honours [server] host/port
# open http://localhost:8000 to watch games
```

Play a demo match (the judge drives both players; `--white`/`--black` take a player
kind: `random`, `greedy`, or `illegal`):
```bash
.venv/bin/python judge/run_match.py --white greedy --black random
```

## Acceptance Criteria

- [x] DB connection (host, port, user, password, database) is configurable via `config.toml`.
      — `config.py`; `python backend/config.py` prints the loaded settings.
- [x] Tables are auto-created in the configured MariaDB database at startup.
      — `db.init_schema()` runs in the FastAPI lifespan; `games` and `moves` confirmed
      present in MariaDB.
- [x] `POST /games` creates a game in the starting position.
      — `TestCreateAndFetch::test_create_returns_starting_position`.
- [x] `POST /games/{id}/moves` accepts legal SAN/UCI moves and rejects illegal ones.
      — `TestMoves`; `400` unparseable, `409` illegal, and rejected moves are not
      recorded. Also rejects the null move `0000`, which `parse_san` otherwise accepts.
- [x] Turn order is enforced by the server (cannot move twice in a row).
      — `TestMoves::test_turn_order_enforced`.
- [x] Checkmate/stalemate/insufficient-material draws are detected and the game is
      marked finished with a result. — `TestGameOver`, `TestGameEnd`.
- [x] Threefold repetition and the 50-move rule are detected — verified by a unit test
      that replays a known repetition sequence and asserts `1/2-1/2`.
      — `test_threefold_repetition_detected`, `test_fifty_move_rule_detected`, plus
      `test_threefold_invisible_without_history`, which pins down why the replay is
      required. A live RandomBot vs GreedyBot game ended by repetition at ply 105.
- [x] Full move history is persisted in MariaDB and returned by `GET /games/{id}`.
      — `test_history_is_persisted_and_ordered`. Integrity checked across all stored
      games: replaying `moves` reproduces the stored FEN and PGN exactly.
- [ ] Web page shows the game list and a live-updating board for a selected game.
      — **Built but not yet confirmed in a browser.** `frontend/` is served and the
      routes return 200; `app.js` passes a syntax check, the pinned chessground 9.2.1
      CDN assets resolve, and the module exports the `Chessground` symbol it imports.
      Still needs one human look to tick this off.
- [x] The judge can run two random players through a complete game with no human input.
      — `judge/run_match.py`; several complete games played, ending in checkmate and
      in repetition.
- [x] A player stubbed to always return an illegal move forfeits after the retry budget,
      ending the game with `termination=forfeit` and the opponent winning.
      — `AlwaysIllegalBot` forfeited after 3 rejections, final result `0-1`.
