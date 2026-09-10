"""`ReservedActivityProjectionService` (Reserved Activities Slice A2):
the read-only Reserved Activities page projection.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively.

Loads exactly ONE `SchedulingProblem` snapshot and derives every list
(`special_activities`, `teachers`, `class_sections`, `days`, `periods`,
`reserved_activities`) from that same in-memory object -- deliberately
never composes `SpecialActivityProjectionService` or any other nested
projection service, each of which would perform its own independent
reload and risk an internally-incoherent page (e.g. a Special Activity
created between two separate reads). `configuration_locked` is
obtained through the normal, separate `ScheduleVersionRepository`
active-schedule check, exactly like every other projection service.
"""
from __future__ import annotations

from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.reserved_activity_projection_models import (
    ReservedActivitiesProjectionView,
    ReservedActivityClassSectionOption,
    ReservedActivityDayOption,
    ReservedActivityItem,
    ReservedActivityPeriodOption,
    ReservedActivityResourceOption,
    ReservedActivitySlotView,
    ReservedActivitySpecialActivityOption,
    ReservedActivityTeacherOption,
)
from school_timetable.domain.activities import ActivityKind


class ReservedActivityProjectionService:
    """Depends only on the two existing repository ports -- never
    SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        schedule_repository: ScheduleVersionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._schedule_repository = schedule_repository

    def project(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ReservedActivitiesProjectionView:
        # (1) Load ONE config snapshot. SchedulingProblemNotFoundError
        # propagates unchanged if the school/year itself does not
        # resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (2) Decision #35's existing gate, reused verbatim -- reads
        # remain available regardless of lock state; only writes reject.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        configuration_locked = active is not None

        # (3) Build every list purely in memory from the same `problem`
        # -- no further DB access, and every order below is already
        # `problem`'s own authoritative order (persistence
        # ordinal/index order); nothing here re-sorts against a
        # different regime.
        special_activities = tuple(
            ReservedActivitySpecialActivityOption(id=a.id, name=a.name)
            for a in problem.activities
            if a.kind == ActivityKind.CLUB
        )
        teachers = tuple(ReservedActivityTeacherOption(id=t.id, name=t.full_name) for t in problem.teachers)
        class_sections = tuple(
            ReservedActivityClassSectionOption(id=c.id, name=c.name) for c in problem.class_sections
        )
        days = tuple(ReservedActivityDayOption(id=d.id, name=d.name, index=d.index) for d in problem.days)
        periods = tuple(
            ReservedActivityPeriodOption(id=p.id, name=p.name, index=p.index, is_instructional=p.is_instructional)
            for p in problem.periods
        )
        reserved_activities = tuple(
            ReservedActivityItem(
                id=block.id,
                special_activity_id=block.activity_id,
                class_section_ids=block.class_sections,
                teacher_id=block.teacher_id,
                slots=tuple(ReservedActivitySlotView(day_id=s.day_id, period_id=s.period_id) for s in block.slots),
                resource_id=block.resource_id,
            )
            for block in problem.reserved_blocks
        )
        # Resources B2: the same Resource catalog option list a "fixed
        # Resource" select needs, in the Resource catalog's own
        # authoritative (persistence ordinal) order -- never re-sorted.
        resources = tuple(
            ReservedActivityResourceOption(id=r.id, name=r.name, capacity=r.capacity) for r in problem.resources
        )

        return ReservedActivitiesProjectionView(
            configuration_locked=configuration_locked,
            special_activities=special_activities,
            teachers=teachers,
            class_sections=class_sections,
            days=days,
            periods=periods,
            reserved_activities=reserved_activities,
            resources=resources,
        )
