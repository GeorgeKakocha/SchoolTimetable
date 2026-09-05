"""SQLAlchemy adapter implementing `application.ports.
SchedulingProblemRepository` (Phase 3A2.3).

Loading strategy is deliberately explicit: resolve `School` and
`AcademicYear` by natural ID first, then issue one `SELECT ... WHERE
academic_year_id = :year_id` per scoped table (no ORM `relationship()`
navigation, no lazy-loading, no hidden N+1 queries), build a
`NaturalIdLookup` from the fetched rows, group child rows by their
parent's surrogate ID in plain Python, and hand everything to Phase
3A2.2's mapper functions. Every top-level tuple is sorted explicitly
before mapping (`Day`/`Period` by `idx`, everything else by `ordinal`)
-- never assumed from database return order.

This module never reimplements preflight/solver/verifier reasoning: it
only reconstructs the persisted configuration. A structurally loadable
but domain-invalid configuration is expected to load successfully here
and be rejected by `validation.preflight.run_preflight` afterward, same
as any other `SchedulingProblem` -- see `docs/DECISIONS.md` #29.

Also defines `SessionFactorySchedulingProblemRepository` (Phase 3A3.2):
a second, session-factory-backed implementation of the same
`SchedulingProblemRepository` Protocol, for future generation use
(Decision #31) -- see its own docstring below. It does not replace or
change `SqlAlchemySchedulingProblemRepository`, which remains exactly as
before for the `/config` read path.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from school_timetable.application.errors import SchedulingProblemNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import mappers as mp
from school_timetable.persistence import models as orm


class SqlAlchemySchedulingProblemRepository:
    """Implements `application.ports.SchedulingProblemRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_by_school_and_year(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> SchedulingProblem:
        session = self._session

        school_row = session.execute(
            select(orm.School).where(orm.School.natural_id == school_natural_id)
        ).scalar_one_or_none()
        if school_row is None:
            raise SchedulingProblemNotFoundError(school_natural_id, academic_year_natural_id)

        year_row = session.execute(
            select(orm.AcademicYear).where(
                orm.AcademicYear.school_id == school_row.id,
                orm.AcademicYear.natural_id == academic_year_natural_id,
            )
        ).scalar_one_or_none()
        if year_row is None:
            raise SchedulingProblemNotFoundError(school_natural_id, academic_year_natural_id)

        year_id = year_row.id

        def _scoped(model):
            return list(
                session.execute(select(model).where(model.academic_year_id == year_id)).scalars()
            )

        day_rows = _scoped(orm.Day)
        period_rows = _scoped(orm.Period)
        class_section_rows = _scoped(orm.ClassSection)
        teacher_rows = _scoped(orm.Teacher)
        activity_rows = _scoped(orm.Activity)
        resource_rows = _scoped(orm.Resource)
        participant_group_rows = _scoped(orm.ParticipantGroup)
        teaching_requirement_rows = _scoped(orm.TeachingRequirement)
        teacher_availability_rows = _scoped(orm.TeacherAvailability)
        reserved_block_rows = _scoped(orm.ReservedBlock)
        fixed_placement_rows = _scoped(orm.FixedPlacement)
        membership_rows = _scoped(orm.ParticipantGroupClassSection)
        time_preference_rows = _scoped(orm.TimePreference)
        reserved_class_rows = _scoped(orm.ReservedBlockClassSection)
        reserved_slot_rows = _scoped(orm.ReservedBlockSlot)

        lookup = mp.NaturalIdLookup.build(
            days=day_rows,
            periods=period_rows,
            class_sections=class_section_rows,
            teachers=teacher_rows,
            activities=activity_rows,
            participant_groups=participant_group_rows,
            resources=resource_rows,
            teaching_requirements=teaching_requirement_rows,
        )

        memberships_by_group = _group_by(membership_rows, "participant_group_id")
        time_prefs_by_requirement = _group_by(time_preference_rows, "teaching_requirement_id")
        reserved_classes_by_block = _group_by(reserved_class_rows, "reserved_block_id")
        reserved_slots_by_block = _group_by(reserved_slot_rows, "reserved_block_id")

        days = tuple(mp.day_to_domain(r) for r in sorted(day_rows, key=lambda r: r.idx))
        periods = tuple(mp.period_to_domain(r) for r in sorted(period_rows, key=lambda r: r.idx))
        class_sections = tuple(
            mp.class_section_to_domain(r) for r in sorted(class_section_rows, key=lambda r: r.ordinal)
        )
        teachers = tuple(mp.teacher_to_domain(r) for r in sorted(teacher_rows, key=lambda r: r.ordinal))
        activities = tuple(mp.activity_to_domain(r) for r in sorted(activity_rows, key=lambda r: r.ordinal))
        resources = tuple(mp.resource_to_domain(r) for r in sorted(resource_rows, key=lambda r: r.ordinal))
        participant_groups = tuple(
            mp.participant_group_to_domain(r, memberships_by_group.get(r.id, []), lookup)
            for r in sorted(participant_group_rows, key=lambda r: r.ordinal)
        )
        teaching_requirements = tuple(
            mp.teaching_requirement_to_domain(r, time_prefs_by_requirement.get(r.id, []), lookup)
            for r in sorted(teaching_requirement_rows, key=lambda r: r.ordinal)
        )
        teacher_availabilities = tuple(
            mp.teacher_availability_to_domain(r, lookup)
            for r in sorted(teacher_availability_rows, key=lambda r: r.ordinal)
        )
        reserved_blocks = tuple(
            mp.reserved_block_to_domain(
                r, reserved_classes_by_block.get(r.id, []), reserved_slots_by_block.get(r.id, []), lookup,
            )
            for r in sorted(reserved_block_rows, key=lambda r: r.ordinal)
        )
        fixed_placements = tuple(
            mp.fixed_placement_to_domain(r, lookup)
            for r in sorted(fixed_placement_rows, key=lambda r: r.ordinal)
        )

        return SchedulingProblem(
            school=mp.school_to_domain(school_row),
            academic_year=mp.academic_year_to_domain(year_row),
            days=days,
            periods=periods,
            teachers=teachers,
            class_sections=class_sections,
            participant_groups=participant_groups,
            activities=activities,
            teaching_requirements=teaching_requirements,
            resources=resources,
            teacher_availabilities=teacher_availabilities,
            reserved_blocks=reserved_blocks,
            fixed_placements=fixed_placements,
        )


def _group_by(rows: list, parent_attr: str) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(getattr(row, parent_attr), []).append(row)
    return grouped


class SessionFactorySchedulingProblemRepository:
    """Session-factory-backed implementation of `application.ports.
    SchedulingProblemRepository` (Phase 3A3.2, Decision #31's
    "SchedulingProblemRepository session-ownership clarification for
    generation") -- for future `GenerateScheduleService` use (Phase
    3A3.3), never wired into `api/` in this phase.

    Unlike `SqlAlchemySchedulingProblemRepository` above, which is
    constructed with an already-open `Session` (correct and unchanged
    for the `/config` read path), this class is constructed with a
    session *factory* and opens/closes its own short `Session` per call
    -- so a caller (e.g. a future generation flow that runs a
    long-running CP-SAT solve after this call returns) never holds a
    database connection open beyond this one load. It deliberately does
    not reimplement the loading logic above: it delegates to
    `SqlAlchemySchedulingProblemRepository`, constructed fresh against
    its own `Session`, which already returns a fully detached
    `SchedulingProblem` of plain frozen domain objects."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def load_by_school_and_year(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> SchedulingProblem:
        session = self._session_factory()
        try:
            return SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id
            )
        finally:
            session.close()
