"""SQLAlchemy adapter implementing `application.ports.
ConfigurationRevisionRepository` (Safe Configuration Changes, Slice B):
the draft configuration lifecycle -- read revision state, open (or
idempotently reuse) an editable draft eagerly cloned from the current
published revision, and discard an open draft.

Session-factory-backed, exactly like `SqlAlchemyTeacherRepository`:
every public method opens and closes its own short `Session`.
`begin_draft`/`discard_draft` each run as one atomic transaction under
the same `AcademicYear` `SELECT ... FOR UPDATE` row lock (Owner
Decision #36) every configuration writer already uses -- via
`configuration_write_lock.resolve_year_id`/`lock_academic_year` --
serializing concurrent calls against each other and against every
other configuration writer/generation's own final persist step, so two
concurrent `begin_draft` calls can never produce two drafts.

Cloning (`begin_draft`'s CASE A) copies every one of the fifteen
`SchedulingProblem` configuration tables from the published revision
into the new draft, in dependency order, remapping every intra-
configuration relationship to the NEWLY-inserted draft rows via
old-surrogate-id -> new-surrogate-id maps built as each table is
cloned -- never copying a published-revision surrogate FK value
directly into a draft row (Slice A's composite FKs would reject that
structurally in any case, since a draft row's `configuration_revision_id`
never matches the published revision's). Natural IDs, and every other
field, are preserved byte-for-byte. `session.flush()` between clone
stages surfaces any composite-FK/uniqueness violation immediately, so
a partially-cloned draft never reaches `session.commit()` --  any
failure rolls back the entire new revision and leaves
`draft_revision_id` unset.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.configuration_revision_models import ConfigurationRevisionState
from school_timetable.application.errors import (
    InitialDraftCannotBeDiscardedError,
    NoConfigurationDraftError,
)
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import lock_academic_year, resolve_year_id


class SqlAlchemyConfigurationRevisionRepository:
    """Implements `application.ports.ConfigurationRevisionRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_state(
        self, school_natural_id: str, academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            year_row = session.execute(
                select(orm.AcademicYear).where(orm.AcademicYear.id == year_id)
            ).scalar_one()
            return _build_state(session, year_id, year_row)
        finally:
            session.close()

    def begin_draft(
        self, school_natural_id: str, academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            year_row = session.execute(
                select(orm.AcademicYear).where(orm.AcademicYear.id == year_id)
            ).scalar_one()

            if year_row.draft_revision_id is None:
                # CASE A only -- CASE B (draft already exists) and CASE C
                # (pre-first-Generate: that IS the draft already exists
                # case) both fall through this whole block untouched,
                # making this method naturally idempotent.
                published_id = year_row.published_revision_id
                if published_id is None:
                    # Unreachable given Slice A's own invariant (every
                    # AcademicYear always has a draft or a published
                    # revision) -- guarded rather than silently cloning
                    # from nothing.
                    raise RuntimeError(
                        f"AcademicYear school={school_natural_id!r}, academic_year="
                        f"{academic_year_natural_id!r} has neither a draft nor a published "
                        "configuration revision to begin editing from"
                    )
                next_revision_number = session.execute(
                    select(func.max(orm.ConfigurationRevision.revision_number)).where(
                        orm.ConfigurationRevision.academic_year_id == year_id,
                    )
                ).scalar_one() + 1
                draft_row = orm.ConfigurationRevision(
                    academic_year_id=year_id, revision_number=next_revision_number, status="DRAFT",
                )
                session.add(draft_row)
                session.flush()

                _clone_configuration_into_draft(session, year_id, published_id, draft_row.id)

                year_row.draft_revision_id = draft_row.id
                session.flush()

            state = _build_state(session, year_id, year_row)
            session.commit()
            return state
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def discard_draft(
        self, school_natural_id: str, academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            year_row = session.execute(
                select(orm.AcademicYear).where(orm.AcademicYear.id == year_id)
            ).scalar_one()

            draft_id = year_row.draft_revision_id
            if draft_id is None:
                raise NoConfigurationDraftError(school_natural_id, academic_year_natural_id)
            if year_row.published_revision_id is None:
                raise InitialDraftCannotBeDiscardedError(school_natural_id, academic_year_natural_id)

            # Defense-in-depth (Slice A's own invariant already says
            # this is unreachable: a revision is never published in
            # place -- `persist_initial_version`/a future regeneration
            # always atomically swap `published_revision_id` to a
            # *different*, freshly-published revision, so no
            # ScheduleVersion is ever created referencing a revision
            # that later becomes -- or already is -- a mutable draft).
            # Checked explicitly rather than relying solely on the
            # RESTRICT/CASCADE FK behavior below to fail loudly.
            referenced = session.execute(
                select(orm.ScheduleVersion.id)
                .where(orm.ScheduleVersion.configuration_revision_id == draft_id)
                .limit(1)
            ).scalar_one_or_none()
            if referenced is not None:
                raise RuntimeError(
                    f"configuration draft for school={school_natural_id!r}, academic_year="
                    f"{academic_year_natural_id!r} is referenced by an existing ScheduleVersion "
                    "and cannot be discarded -- unreachable under Slice A's own invariant"
                )

            # Clear the pointer before deleting the row it points to --
            # `fk_academic_year_draft_revision` would otherwise reject
            # deleting a still-referenced ConfigurationRevision.
            year_row.draft_revision_id = None
            session.flush()
            draft_row = session.get(orm.ConfigurationRevision, draft_id)
            session.delete(draft_row)
            state = _build_state(session, year_id, year_row)
            session.commit()
            return state
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _build_state(session: Session, year_id: int, year_row: orm.AcademicYear) -> ConfigurationRevisionState:
    published_number = _revision_number_or_none(session, year_row.published_revision_id)
    draft_number = _revision_number_or_none(session, year_row.draft_revision_id)
    has_schedule = session.execute(
        select(orm.Schedule.id).where(orm.Schedule.academic_year_id == year_id)
    ).scalar_one_or_none() is not None
    return ConfigurationRevisionState(
        published_revision_number=published_number,
        draft_revision_number=draft_number,
        has_schedule=has_schedule,
        configuration_locked=draft_number is None,
        timetable_out_of_date=has_schedule and draft_number is not None,
    )


def _revision_number_or_none(session: Session, revision_id: int | None) -> int | None:
    if revision_id is None:
        return None
    return session.execute(
        select(orm.ConfigurationRevision.revision_number).where(orm.ConfigurationRevision.id == revision_id)
    ).scalar_one()


def _clone_table(
    session: Session,
    model: type,
    year_id: int,
    source_revision_id: int,
    draft_revision_id: int,
    build_fields: Callable[[Any], dict[str, Any]],
) -> dict[int, int]:
    """Clones every `model` row scoped to `source_revision_id` into
    `draft_revision_id`. `build_fields(old_row)` returns every
    constructor field to copy/remap except `academic_year_id`/
    `configuration_revision_id`, which are always set here, uniformly.
    Returns old-surrogate-id -> new-surrogate-id for tables with a
    surrogate `id` primary key (every one of the nine tables another
    table's FK can reference); an empty dict for the six composite-PK
    leaf tables, which nothing else needs to remap by."""
    old_rows = list(session.execute(
        select(model).where(
            model.academic_year_id == year_id,
            model.configuration_revision_id == source_revision_id,
        )
    ).scalars())

    pairs: list[tuple[Any, Any]] = []
    for old_row in old_rows:
        new_row = model(
            academic_year_id=year_id,
            configuration_revision_id=draft_revision_id,
            **build_fields(old_row),
        )
        session.add(new_row)
        pairs.append((old_row, new_row))
    session.flush()

    return {old_row.id: new_row.id for old_row, new_row in pairs if hasattr(old_row, "id")}


def _clone_configuration_into_draft(
    session: Session, year_id: int, source_revision_id: int, draft_revision_id: int,
) -> None:
    """Dependency-ordered clone of all fifteen `SchedulingProblem`
    configuration tables -- the nine tables another table's FK can
    reference first (in an order where each one's own FK targets, if
    any, are already cloned), then the six leaf/join tables."""
    day_map = _clone_table(
        session, orm.Day, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(natural_id=r.natural_id, name=r.name, idx=r.idx),
    )
    period_map = _clone_table(
        session, orm.Period, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            natural_id=r.natural_id, name=r.name, idx=r.idx, block_id=r.block_id,
            is_instructional=r.is_instructional, start_time=r.start_time, end_time=r.end_time,
        ),
    )
    class_section_map = _clone_table(
        session, orm.ClassSection, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(natural_id=r.natural_id, name=r.name, ordinal=r.ordinal),
    )
    teacher_map = _clone_table(
        session, orm.Teacher, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            natural_id=r.natural_id, first_name=r.first_name, last_name=r.last_name, ordinal=r.ordinal,
        ),
    )
    activity_map = _clone_table(
        session, orm.Activity, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(natural_id=r.natural_id, name=r.name, kind=r.kind, ordinal=r.ordinal),
    )
    resource_map = _clone_table(
        session, orm.Resource, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(natural_id=r.natural_id, name=r.name, capacity=r.capacity, ordinal=r.ordinal),
    )
    participant_group_map = _clone_table(
        session, orm.ParticipantGroup, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(natural_id=r.natural_id, name=r.name, role=r.role, ordinal=r.ordinal),
    )
    requirement_map = _clone_table(
        session, orm.TeachingRequirement, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            natural_id=r.natural_id,
            teacher_id=teacher_map[r.teacher_id],
            activity_id=activity_map[r.activity_id],
            participant_group_id=participant_group_map[r.participant_group_id],
            weekly_periods=r.weekly_periods,
            block_mode=r.block_mode,
            block_sizes=list(r.block_sizes),
            min_distinct_days=r.min_distinct_days,
            max_periods_per_day=r.max_periods_per_day,
            resource_id=None if r.resource_id is None else resource_map[r.resource_id],
            split_group_id=r.split_group_id,
            ordinal=r.ordinal,
        ),
    )
    reserved_block_map = _clone_table(
        session, orm.ReservedBlock, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            natural_id=r.natural_id,
            name=r.name,
            activity_id=activity_map[r.activity_id],
            teacher_id=None if r.teacher_id is None else teacher_map[r.teacher_id],
            resource_id=None if r.resource_id is None else resource_map[r.resource_id],
            ordinal=r.ordinal,
        ),
    )
    _clone_table(
        session, orm.ParticipantGroupClassSection, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            participant_group_id=participant_group_map[r.participant_group_id],
            class_section_id=class_section_map[r.class_section_id],
            ordinal=r.ordinal,
        ),
    )
    _clone_table(
        session, orm.TeacherAvailability, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            teacher_id=teacher_map[r.teacher_id],
            day_id=day_map[r.day_id],
            period_id=period_map[r.period_id],
            status=r.status,
            ordinal=r.ordinal,
        ),
    )
    _clone_table(
        session, orm.TimePreference, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            teaching_requirement_id=requirement_map[r.teaching_requirement_id],
            ordinal=r.ordinal,
            preferred_period_indexes=list(r.preferred_period_indexes),
            weight=r.weight,
        ),
    )
    _clone_table(
        session, orm.ReservedBlockClassSection, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            reserved_block_id=reserved_block_map[r.reserved_block_id],
            class_section_id=class_section_map[r.class_section_id],
            ordinal=r.ordinal,
        ),
    )
    _clone_table(
        session, orm.ReservedBlockSlot, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            reserved_block_id=reserved_block_map[r.reserved_block_id],
            day_id=day_map[r.day_id],
            period_id=period_map[r.period_id],
            ordinal=r.ordinal,
        ),
    )
    _clone_table(
        session, orm.FixedPlacement, year_id, source_revision_id, draft_revision_id,
        lambda r: dict(
            natural_id=r.natural_id,
            teaching_requirement_id=requirement_map[r.teaching_requirement_id],
            day_id=day_map[r.day_id],
            period_id=period_map[r.period_id],
            ordinal=r.ordinal,
        ),
    )
