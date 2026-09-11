"""Plain, application-owned request/result shapes for Calendar A's Day/
Period write use cases -- never an ORM row, never Pydantic, matching
`resource_models.py`'s exact discipline.

`Day`/`Period` are not new/redesigned domain entities -- they are the
same `domain.calendar.Day`/`Period` that already flow through the
solver/preflight/verifier/persistence layers; this slice only adds the
missing write (catalog CRUD) surface over them, plus two new optional
`Period.start_time`/`end_time` fields. `block_id` is never part of this
public write surface -- callers submit `starts_new_block: bool`
instead (see `calendar_rules`/`domain.calendar.recompute_block_ids`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class DayFields:
    """The one owner-approved editable Day field -- the exact input
    shape for both create and update. Raw numeric `index` is never a
    normal edit field (section 5) -- ordering is changed only via
    `CalendarService.day_move`."""

    name: str


@dataclass(frozen=True)
class DayWriteResult:
    id: str
    name: str
    index: int


@dataclass(frozen=True)
class PeriodFields:
    """The owner-approved editable Period fields for both create and
    update. `is_instructional` is deliberately absent from this public
    write surface (locked policy, section "IMPORTANT is_instructional
    POLICY") -- new Periods are always instructional, and an existing
    legacy `is_instructional=False` row's own flag is preserved
    unchanged by `CalendarService`/`calendar_repository`, never
    editable through this contract."""

    name: str
    start_time: time | None
    end_time: time | None
    starts_new_block: bool


@dataclass(frozen=True)
class PeriodWriteResult:
    id: str
    name: str
    index: int
    start_time: time | None
    end_time: time | None
    starts_new_block: bool
    is_instructional: bool
