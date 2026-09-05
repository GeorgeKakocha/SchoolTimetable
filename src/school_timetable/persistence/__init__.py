"""Persistence layer (Phase 3): SQLAlchemy engine/session infrastructure
and, starting Phase 3A2, ORM models and repository adapters implementing
`application/`'s repository ports.

Deliberately not imported by `domain/`, `scheduling/`, `validation/`, or
`verification/` -- see docs/ARCHITECTURE.md.
"""
from __future__ import annotations

# Importing `models` here (rather than in `migrations/env.py`) is what
# makes `env.py`'s "every future model lives on this same Base, picked up
# automatically" comment true: `env.py` imports `persistence.base.Base`,
# which first runs this package's `__init__.py`, registering every table
# on `Base.metadata` before Alembic ever inspects it.
from school_timetable.persistence import models as models  # noqa: F401
