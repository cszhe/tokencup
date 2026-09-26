# Notes for AI agents

Human-facing docs are in [README.md](README.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). This file covers maintainer tasks that
agents are asked to do repeatedly. (If you are here to referee a match, read
[judge/JUDGE_AGENT.md](judge/JUDGE_AGENT.md) instead.)

## Releasing a new sample database snapshot

Do this when the user asks to update or release the database dump, typically
after more games have been played. Always use `sample-data/make_dump.py`.
Don't hand-write SQL or use `mysqldump`: the script keeps the file format
stable, so each release is an append-only diff that is easy to review.

1. **Regenerate and verify the dump.**

   ```bash
   .venv/bin/python sample-data/make_dump.py --verify
   ```

   The script reads the database named in `backend/config.toml` and writes
   `sample-data/tokencup_sample.sql` with every finished game. It checks
   that games already in the dump are unchanged and updates the
   "N games, N moves" counts in `README.md` and `sample-data/README.md`.
   `--verify` then loads the result into a throwaway MariaDB container, which
   needs Docker, and checks the row counts. The script prints the new games
   as a Markdown table for the release notes. Use `--dry-run` to look before
   writing anything.

   Stop and check with the user in these cases:
   - **"Skipping unfinished game":** a match is probably still in progress.
     Ask whether to wait for it to finish.
   - **"Refusing to write":** a game from an earlier release changed or
     disappeared. Show the user the diff it printed, and rerun with
     `--allow-changes` only if they confirm the change is intended.
   - **"Nothing new to release":** there is nothing to do. Don't cut a
     release.

2. **Review `git diff`.** The dump should only gain rows, plus a single `;` →
   `,` where the games `INSERT` continues. The only other changes should be
   the count lines in the two READMEs. The new rows should hold nothing but
   AI model names and chess data. If a player name looks like a real person,
   raise it with the user before publishing.

3. **Commit directly to `main` and push.** `main` isn't protected, and
   dump updates don't go through a PR. Use a message like
   `Update sample database dump with N new games` and name the new games in
   the body.

4. **Create the GitHub release.** Tags are plain integers (`v1`, `v2`, …).
   Run `gh release list` and take the next one.

   ```bash
   gh release create vN --target "$(git rev-parse HEAD)" \
     --title "Sample data update: <total> games" --notes-file <notes.md>
   ```

   `--target` must be the **full** SHA. A short SHA fails with
   `HTTP 422: tag_name is not a valid tag`, and nothing gets created.

   Notes template:

   ```markdown
   ## Sample data update

   `sample-data/tokencup_sample.sql` now has <k> more games, for a total of **<n> games and <m> moves**:

   <the table make_dump.py printed>

   No schema changes; the existing <n-k> games are unchanged. Load it the same way as before (see [sample-data/README.md](https://github.com/cszhe/tokencup/blob/vN/sample-data/README.md)).

   **Full Changelog**: https://github.com/cszhe/tokencup/compare/vPREV...vN
   ```

   If the schema in `backend/db.py` changed since the last release, say so in
   the notes instead of "No schema changes". An old dump will not match the
   new tables.

5. **Confirm CI passed:** `gh run list --limit 1`.
