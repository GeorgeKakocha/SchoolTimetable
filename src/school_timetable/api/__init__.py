"""FastAPI application (Phase 3).

No business/domain endpoints exist yet (Phase 3A1) -- just the app shell
and a real, DB-backed health check. This package must never import
`scheduling/` or `fixtures/` directly; from Phase 3A2 onward it depends
only on `application/` services, never on `persistence/` internals
(construction of concrete repository adapters happens in one composition
root, not scattered through route handlers).
"""
from __future__ import annotations
