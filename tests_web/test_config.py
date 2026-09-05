"""Settings/configuration tests -- no database needed."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from school_timetable.config import Settings, get_settings


def test_database_url_has_no_hard_coded_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_url_reads_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@example.invalid:5432/somedb")
    settings = get_settings()
    assert settings.database_url == "postgresql+psycopg://u:p@example.invalid:5432/somedb"


def test_get_settings_is_not_stale_across_env_changes(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:a@example.invalid:5432/a")
    first = get_settings().database_url
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://b:b@example.invalid:5432/b")
    second = get_settings().database_url
    assert first != second
