"""Shared fixtures for the web/persistence test suite.

Deliberately a separate suite from `tests/` (the pure domain/solver
suite, which needs no database and no `web` extras): `tests/` stays
exactly as fast and dependency-free as it is today, and this suite is
run explicitly (`pytest -q tests_web`) by a developer/CI job that has
both the `web` extras and a real PostgreSQL instance available (see
docker-compose.yml). It is never collected by a plain `pytest -q`
(pyproject.toml's `testpaths` points only at `tests/`).

Tests here that need a live database skip cleanly (not fail) when one
isn't reachable, so this suite behaves correctly whether or not
docker-compose's `db` service happens to be running.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from school_timetable.persistence.db import create_db_engine


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://school_timetable:school_timetable@localhost:5432/school_timetable_test",
    )


@pytest.fixture
def live_db_engine():
    """A real Engine bound to TEST_DATABASE_URL, skipping the test
    (never failing it) if no database is actually reachable there.

    Function-scoped, so every test gets its own `Engine`/connection
    pool -- but ownership of that `Engine` stays with this fixture for
    its whole lifetime: it is always disposed on exit, whether the
    reachability probe below fails (skip), the test passes, or the test
    raises. Without this, each test's `Engine` (and the real
    server-side connections its pool opened) would never be released,
    and server-side connections would accumulate monotonically across a
    full suite run until PostgreSQL's `max_connections` is exhausted."""
    engine = create_db_engine(_test_database_url())
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any connectivity failure means "skip"
        engine.dispose()
        pytest.skip(f"no reachable PostgreSQL at {_test_database_url()!r} ({exc.__class__.__name__}); "
                    f"start it with `docker compose up -d db` to run this test")
    try:
        yield engine
    finally:
        engine.dispose()
