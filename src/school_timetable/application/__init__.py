"""Application layer (Phase 3A2.3): repository ports/interfaces that
`persistence/` adapters implement, and the errors those ports raise.

Depends only on `domain/` -- never on SQLAlchemy, `persistence/`,
`fixtures/`, or FastAPI, even transitively (see `docs/DECISIONS.md` #27
and `docs/ARCHITECTURE.md`'s locked ports-and-adapters direction).
"""
from __future__ import annotations
