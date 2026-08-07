"""Configuration loading for TokenCup.

Resolution order for the config file path:
    1. an explicit path passed to load_config()   (e.g. from a --config CLI flag)
    2. the TOKENCUP_CONFIG environment variable
    3. backend/config.toml, next to this file
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.toml"
ENV_VAR = "TOKENCUP_CONFIG"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class Config:
    database: DatabaseConfig
    server: ServerConfig
    path: Path


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Return the config file path per the documented resolution order."""
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_PATH


def _require(table: dict, section: str, key: str, expected: type, path: Path):
    if key not in table:
        raise ConfigError(f"{path}: missing required key [{section}].{key}")
    value = table[key]
    # bool is a subclass of int, so reject it explicitly for numeric fields.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ConfigError(
            f"{path}: [{section}].{key} must be {expected.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """Load and validate the TOML config file."""
    path = resolve_config_path(explicit)
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}\n"
            f"Copy {DEFAULT_CONFIG_PATH.parent / 'config.example.toml'} "
            f"to {DEFAULT_CONFIG_PATH} and fill in your credentials."
        )

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    for section in ("database", "server"):
        if not isinstance(raw.get(section), dict):
            raise ConfigError(f"{path}: missing required [{section}] section")

    db, srv = raw["database"], raw["server"]

    return Config(
        database=DatabaseConfig(
            host=_require(db, "database", "host", str, path),
            port=_require(db, "database", "port", int, path),
            user=_require(db, "database", "user", str, path),
            password=_require(db, "database", "password", str, path),
            database=_require(db, "database", "database", str, path),
        ),
        server=ServerConfig(
            host=_require(srv, "server", "host", str, path),
            port=_require(srv, "server", "port", int, path),
        ),
        path=path,
    )


if __name__ == "__main__":
    # Smoke check: python config.py  -> prints the loaded config, password redacted.
    cfg = load_config()
    print(f"loaded {cfg.path}")
    print(
        f"  database: {cfg.database.user}@{cfg.database.host}:{cfg.database.port}"
        f"/{cfg.database.database} (password: {'set' if cfg.database.password else 'EMPTY'})"
    )
    print(f"  server:   {cfg.server.host}:{cfg.server.port}")
