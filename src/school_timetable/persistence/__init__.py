"""Persistence layer (Phase 3): SQLAlchemy engine/session infrastructure
and, starting Phase 3A2, ORM models and repository adapters implementing
`application/`'s repository ports.

Deliberately not imported by `domain/`, `scheduling/`, `validation/`, or
`verification/` -- see docs/ARCHITECTURE.md.
"""
from __future__ import annotations
