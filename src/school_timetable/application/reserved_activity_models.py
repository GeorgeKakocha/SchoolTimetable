"""Plain, application-owned request/result shapes for the Reserved
Activities Slice A2 Reserved Activity write use case -- never an ORM
row, never Pydantic, matching `subject_models.py`/
`special_activity_models.py`'s exact discipline.

A "Reserved Activity" is not a new domain entity -- it is the
user-facing name for `ReservedBlock` (see `domain/blocks.py`). These
models never carry `name`: `ReservedBlock.name` is server-derived from
the referenced Special Activity's own `name`, never independent user
input.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReservedActivitySlotFields:
    day_id: str
    period_id: str


@dataclass(frozen=True)
class ReservedActivityFields:
    """The complete mutable aggregate -- the exact input shape for both
    create and update (PUT is always a whole-aggregate replacement,
    never a partial patch)."""

    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotFields, ...]


@dataclass(frozen=True)
class ReservedActivityWriteResult:
    """The written `ReservedBlock`'s own resolved fields, already in
    canonical persisted order. No `name` -- internal/derived, never
    exposed on this surface."""

    id: str
    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotFields, ...]
