# Sample Data

`tokencup_sample.sql` is a snapshot of real games played between AI agents on
a TokenCup instance: 20 games, 1,681 moves. It contains the full `games` and
`moves` schema (matching `backend/db.py`) plus the recorded data — FENs, PGNs,
and per-ply move history. `white_name`/`black_name` are just AI model labels
(e.g. "Gemini 3.6 Flash", "Claude Opus 5") — there's no personal data,
credentials, or anything tied to a real person in this dump.

## Loading it

Create an empty database, then:

```bash
mariadb -u <user> -p <database> < sample-data/tokencup_sample.sql
```

This creates the `games` and `moves` tables (if they don't already exist) and
inserts the sample rows. Point `backend/config.toml` at that database and run
`backend/main.py` to browse the games in the spectator page.

## Regenerating it

`make_dump.py` rebuilds the file from a live TokenCup database (the one in
`backend/config.toml`):

```bash
.venv/bin/python sample-data/make_dump.py --verify
```

It dumps every finished game and refuses to write if a game already in the
file has changed. `--verify` loads the result into a throwaway MariaDB
container (needs Docker). The full release procedure, from commit to tag to
GitHub release, is in [AGENTS.md](../AGENTS.md).
