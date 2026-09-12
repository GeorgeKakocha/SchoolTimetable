"""Shared scheduling-configuration write-lock primitives (Real-School
Setup MVP Slice B), extracted from `teaching_assignment_repository.py`
so every configuration writer -- Teaching Assignments, Teacher CRUD,
and any future one -- uses the *same* authoritative `AcademicYear` row
lock and Owner-Decision-#35 recheck. Never anything more than that:
this is persistence-private plumbing, not a generic ORM CRUD base or a
model-agnostic repository framework. `_natural_to_surrogate`/ordinal
helpers stay local to each repository -- they are not genuinely shared
across every configuration writer the way this lock sequence is.

Every function here is a plain SQLAlchemy `Session` operation, never
exposed to `application/`/`domain/`/`api/` -- only imported by
`persistence/` adapters.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from school_timetable.application.errors import ConfigurationLockedError, SchedulingProblemNotFoundError
from school_timetable.persistence import models as orm


def resolve_year_id(session: Session, school_natural_id: str, academic_year_natural_id: str) -> int:
    year_id = session.execute(
        select(orm.AcademicYear.id)
        .join(orm.School, orm.School.id == orm.AcademicYear.school_id)
        .where(
            orm.School.natural_id == school_natural_id,
            orm.AcademicYear.natural_id == academic_year_natural_id,
        )
    ).scalar_one_or_none()
    if year_id is None:
        raise SchedulingProblemNotFoundError(school_natural_id, academic_year_natural_id)
    return year_id


def lock_academic_year(session: Session, year_id: int) -> None:
    """Owner Decision #36: a short, exclusive row lock on this
    `AcademicYear`, held only for the remainder of the caller's
    transaction -- shared by every scheduling-configuration writer
    (`persist_initial_version`'s own pre-persist lock, every Teaching
    Assignment write, every Teacher write), serializing them all
    against each other and against an in-flight generation's final
    persist step."""
    session.execute(select(orm.AcademicYear.id).where(orm.AcademicYear.id == year_id).with_for_update())


def reject_if_configuration_locked(
    session: Session, year_id: int, school_natural_id: str, academic_year_natural_id: str,
) -> None:
    """Authoritative Decision #35 recheck -- performed *after* acquiring
    the Decision #36 lock, never relying solely on an earlier, un-locked
    caller precheck (each service's own fast-fail check is exactly
    that: a precheck, not the concurrency guarantee).

    Safe Configuration Changes, Slice B: configuration is locked exactly
    when this `AcademicYear` has no open DRAFT `ConfigurationRevision`
    -- NOT whenever a `Schedule` exists. Before Slice B those two
    conditions were identical (a year's draft was cleared the moment
    its first `Generate` published it, and never set again); Slice B's
    "Begin editing configuration" (`ConfigurationRevisionRepository.
    begin_draft`) reopens a draft for a year that already has a
    `Schedule`, and configuration writes must succeed again once it
    does -- redirected to that draft by `resolve_draft_revision_id`
    below, never touching the immutable published revision or any
    `Schedule`/`ScheduleVersion` row."""
    draft_exists = session.execute(
        select(orm.AcademicYear.draft_revision_id).where(orm.AcademicYear.id == year_id)
    ).scalar_one() is not None
    if not draft_exists:
        session.rollback()
        raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)


def resolve_draft_revision_id(
    session: Session, year_id: int, school_natural_id: str, academic_year_natural_id: str,
) -> int:
    """The `ConfigurationRevision` every configuration write must stamp
    its new/edited row with. Called only after
    `reject_if_configuration_locked` has already confirmed a draft is
    open for this year, so this never returns `None` on any reachable
    path -- guarded below anyway rather than silently writing a row
    under no revision at all."""
    draft_revision_id = session.execute(
        select(orm.AcademicYear.draft_revision_id).where(orm.AcademicYear.id == year_id)
    ).scalar_one()
    if draft_revision_id is None:
        # Unreachable given the precondition above -- guarded rather
        # than silently writing a row under no revision at all.
        raise RuntimeError(
            f"AcademicYear school={school_natural_id!r}, academic_year={academic_year_natural_id!r} "
            "has no draft configuration revision to write into"
        )
    return draft_revision_id
