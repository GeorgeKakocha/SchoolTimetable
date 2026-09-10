"""Plain, application-owned request/result shapes for the Resources
Slice A Resource catalog write use case -- never an ORM row, never
Pydantic, matching `special_activity_models.py`/`subject_models.py`'s
exact discipline.

`Resource` is not a new/redesigned domain entity -- it is the same
`domain.resources.Resource(id, name, capacity=1)` that already flows
through the solver/preflight/verifier/persistence layers; this slice
only adds the missing write (catalog CRUD) surface over it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceFields:
    """The two owner-approved editable fields -- the exact input shape
    for both create and update."""

    name: str
    capacity: int


@dataclass(frozen=True)
class ResourceWriteResult:
    """The written `Resource`'s own resolved fields."""

    id: str
    name: str
    capacity: int
