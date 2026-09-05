"""SQLAlchemy engine/session plumbing tests -- require a live PostgreSQL
(TEST_DATABASE_URL / docker-compose's `db` service); skip cleanly if
unreachable rather than failing the whole suite in an environment with
no database (see conftest.py)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


def test_engine_executes_a_real_round_trip(live_db_engine):
    with live_db_engine.connect() as conn:
        result = conn.execute(text("SELECT 1 AS value"))
        assert result.scalar_one() == 1


def test_session_factory_opens_and_closes_cleanly(live_db_engine):
    session_local = sessionmaker(bind=live_db_engine)
    session = session_local()
    try:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        session.close()
