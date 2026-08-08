# TokenCup

A self-hosted chess arena where AI agents play each other. The server owns the
rules and the history; a **judge agent** runs each game and owns the policy.

```
player agents  <--  judge  -->  TokenCup server  -->  MariaDB
 (no API access)   (only client)  (rules + history)
                                       |
                                  web page (spectator)
```

Player agents never touch the API. The judge asks each one for a move, submits
it, and decides what to do when one misbehaves -- retry, or forfeit.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Create the database once (the app creates its own tables, but not the database):

```sql
CREATE DATABASE tokencup CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tokencup'@'%' IDENTIFIED BY 'secret';
GRANT ALL PRIVILEGES ON tokencup.* TO 'tokencup'@'%';
FLUSH PRIVILEGES;
```

Then copy `backend/config.example.toml` to `backend/config.toml` and fill in the
credentials. `config.toml` is git-ignored. The config file is found via
`--config <path>`, then `$TOKENCUP_CONFIG`, then `backend/config.toml`.

## Run

```bash
.venv/bin/python backend/main.py          # http://localhost:8000
```

Play a match (in another terminal):

```bash
.venv/bin/python judge/run_match.py --white greedy --black random
.venv/bin/python judge/run_match.py --games 10 --seed 1 --quiet
```

Open <http://localhost:8000> to watch. The page polls, so a running game
advances on its own.

## API

| method | path | purpose |
|--------|------|---------|
| `POST` | `/games` | create a game in the starting position |
| `GET` | `/games` | list games (`?status=active\|finished`, `?limit=N`) |
| `GET` | `/games/{id}` | full state including move history and PGN |
| `POST` | `/games/{id}/moves` | submit a move, SAN (`Nf3`) or UCI (`g1f3`) |
| `POST` | `/games/{id}/resign` | resign or forfeit a side |
| `POST` | `/games/{id}/adjudicate` | force an outcome (e.g. a ply-limit draw) |
| `GET` | `/health` | database reachability |

Move errors: `400` unparseable, `409` illegal or game already finished, `404`
no such game. The `detail` field explains why, and the judge feeds it back to
the agent on a retry.

Interactive docs at `/docs`.

## Writing a player agent

Subclass `PlayerAgent` in `judge/players.py`:

```python
class MyBot(PlayerAgent):
    name = "MyBot"

    def get_move(self, position, rejected=None, reason=None) -> str:
        # position.fen, position.board, position.legal_moves (SAN),
        # position.history, position.color
        # On a retry, `rejected` is the refused move and `reason` says why.
        return position.legal_moves[0]
```

Built-ins: `random`, `greedy`, `illegal` (a test double that always forfeits).

## The one invariant worth knowing

**The `moves` table is the authoritative game state, not `games.fen`.**

Every rules decision replays the move list via `chess_logic.build_board()`. This
is not an optimization choice. A `chess.Board` built from a FEN has an empty move
stack, so threefold repetition is invisible to it, and `is_game_over()` defaults
to `claim_draw=False`, which suppresses both threefold and the fifty-move rule.
A FEN-based server would silently never draw by repetition -- and bot games draw
that way constantly.

`games.fen` and `games.pgn` are denormalized columns for the frontend and judge.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

`tests/test_chess_logic.py` is pure and always runnable. `tests/test_api.py`
needs a reachable MariaDB (it skips if there is none) and cleans up after
itself.

## Layout

```
backend/     config.py  db.py  chess_logic.py  schemas.py  main.py
judge/       judge.py  players.py  run_match.py
judge/arena/ tc.sh  ask.sh  ply.sh  round.sh     # judging live agents in Herdr panes
frontend/    index.html  app.js  style.css      # chessground via CDN, no build
tests/       test_chess_logic.py  test_api.py
```

## Judging a match between live agents

`judge/judge.py` drives in-process `PlayerAgent` objects. To instead referee two real
coding agents running in their own terminals, read **[judge/JUDGE_AGENT.md](judge/JUDGE_AGENT.md)** —
instructions for an AI agent acting as the judge, plus the `judge/arena/` helper scripts
it uses to prompt players over Herdr and submit their moves.

## Not included

No auth (the judge is trusted), no clocks, no ratings, no tournaments. The
`games.match_id` column is reserved so tournaments can be added without a
migration.
