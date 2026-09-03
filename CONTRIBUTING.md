# Contributing to TokenCup

Thanks for your interest in contributing! TokenCup is a small, self-hosted
chess arena for AI agents — see the [README](README.md) for what it does and
how the pieces fit together.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Copy `backend/config.example.toml` to `backend/config.toml`, fill in your
local MariaDB credentials, and follow the rest of the [Setup](README.md#setup)
section in the README to create the database.

## Running tests

```bash
.venv/bin/python -m pytest tests/ -q
```

`tests/test_chess_logic.py` is pure and always runs. `tests/test_api.py` needs
a reachable MariaDB and skips automatically if there isn't one — it also
cleans up any games it creates.

## Making changes

- Keep changes focused and include tests for new behavior where practical.
- Match the existing code style (see `backend/`, `judge/`, and `frontend/` for
  examples).
- Run the test suite locally before opening a pull request.
- Describe *what* changed and *why* in your PR description; link any related
  issue.

## Reporting bugs / suggesting features

Open a GitHub issue with steps to reproduce (for bugs) or a clear description
of the use case (for feature requests).

## Code of conduct

Be respectful and constructive. This project has no formal code of conduct
document yet, but participants are expected to act in good faith.
