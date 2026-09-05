"""GET /health tests.

Covers both directions deliberately: the endpoint must genuinely attempt
a database round trip, not fake success, so both "database reachable"
and "database unreachable" are exercised as real behavior rather than
assumed.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from school_timetable.api.main import app
from school_timetable.persistence.db import create_db_engine, get_session


def test_health_reports_503_when_database_unreachable():
    broken_engine = create_db_engine("postgresql+psycopg://nobody:nobody@127.0.0.1:1/nowhere")
    broken_session_local = sessionmaker(bind=broken_engine)

    def override_get_session():
        session = broken_session_local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert "unreachable" in body["database"]


def test_health_reports_ok_when_database_reachable(live_db_engine):
    live_session_local = sessionmaker(bind=live_db_engine)

    def override_get_session():
        session = live_session_local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
