"""Runtime configuration for the web/persistence layer (Phase 3).

Deliberately separate from the pure domain/solver model: `domain/`,
`scheduling/`, `validation/`, and `verification/` read nothing from the
environment and must never import this module. This is the one place
`DATABASE_URL` (and any future web/persistence-only setting) is read from
the environment / a local `.env` file -- see `.env.example`.

Credential policy (see `docs/DECISIONS.md` #24): this module -- the
Python application configuration -- must never hard-code a DATABASE_URL,
username, password, or other credential default; `database_url` below is
required from the environment/`.env` for exactly that reason.
`.env.example` and `docker-compose.yml` are a different, narrower case:
they may (and do) document clearly-labeled local-development placeholder
values, gitignored (`.env`) or environment-overridable
(`docker-compose.yml`) respectively, never presented as production
credentials. Real production credentials are never committed anywhere in
this repository.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    """SQLAlchemy-format connection string. PostgreSQL is the only
    supported/authoritative engine -- see docs/ARCHITECTURE.md. No default
    is defined here on purpose: this Python configuration must not
    hard-code a DATABASE_URL or any credential, even a local-development
    placeholder. Every environment (including local development) must set
    DATABASE_URL explicitly, via `.env` (copy `.env.example`, which does
    document local-development placeholder values) or the environment --
    see docker-compose.yml for the matching local development values."""


def get_settings() -> Settings:
    """Not cached: constructing `Settings` just reads a few environment
    variables, which is cheap, and an uncached read keeps tests (which
    routinely monkeypatch the environment) simple and surprise-free."""
    return Settings()
