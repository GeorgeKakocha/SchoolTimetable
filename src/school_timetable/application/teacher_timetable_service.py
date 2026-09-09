"""`TeacherTimetableService` (next product slice after Phase 3C.3, no
new phase number): projects the active generated schedule into one
`Teacher`'s ordered timetable grid.

Mirrors `class_timetable_service.py::ClassTimetableService` exactly --
depends only on the two existing application-owned repository ports
(`application.ports`), never SQLAlchemy, persistence concrete
adapters, ORM models, FastAPI, or API schemas, even transitively. No DB
`Session`/connection is held by this service itself: both repository
calls finish (open and close their own short session internally)
before any of this module's own, pure, in-memory projection logic
runs. No solver call, no verifier call, no repository write.

Membership (which entries belong to this teacher), ordering, and
name/role resolution are all decided here, once, in the backend --
never by a caller (React) -- matching the exact architecture boundary
`ClassTimetableService` already established.
"""
from __future__ import annotations

from school_timetable.application.errors import TeacherNotFoundError
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.teacher_timetable_models import (
    DayHeader,
    TeacherTimetableCell,
    TeacherTimetableClassSection,
    TeacherTimetableEntry,
    TeacherTimetableRow,
    TeacherTimetableView,
)
from school_timetable.domain.groups import ParticipantGroup
from school_timetable.domain.result import ScheduleEntry


class TeacherTimetableService:
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
        teacher_id: str,
    ) -> TeacherTimetableView | None:
        # (1) Load config. SchedulingProblemNotFoundError propagates
        # unchanged if the school/year itself does not resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (2) Resolve the requested teacher from config -- a
        # caller-supplied bad teacher ID is a distinct, narrower
        # "not found" than an unknown school/year.
        teacher = next((t for t in problem.teachers if t.id == teacher_id), None)
        if teacher is None:
            raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_id)

        # (3) Load the active schedule. None means "no Schedule has
        # been generated yet" -- an ordinary, expected outcome, not an
        # error.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if active is None:
            return None

        # (4) Build the view model purely in memory -- no further DB access.
        days = tuple(
            DayHeader(id=d.id, name=d.name) for d in sorted(problem.days, key=lambda d: d.index)
        )
        periods = sorted(
            (p for p in problem.periods if p.is_instructional), key=lambda p: p.index,
        )

        activities_by_id = {a.id: a.name for a in problem.activities}
        groups_by_id = {g.id: g for g in problem.participant_groups}
        class_sections_by_id = {c.id: c.name for c in problem.class_sections}

        cells: dict[tuple[str, str], list[TeacherTimetableEntry]] = {}
        for entry in active.entries:
            if entry.teacher_id != teacher_id:
                continue
            key = (entry.day_id, entry.period_id)
            cells.setdefault(key, []).append(
                _project_entry(entry, activities_by_id, groups_by_id, class_sections_by_id)
            )

        rows = tuple(
            TeacherTimetableRow(
                period_id=period.id,
                period_name=period.name,
                cells=tuple(
                    TeacherTimetableCell(
                        day_id=day.id,
                        entries=tuple(cells.get((day.id, period.id), ())),
                    )
                    for day in days
                ),
            )
            for period in periods
        )

        return TeacherTimetableView(
            school_id=problem.school.id,
            school_name=problem.school.name,
            academic_year_id=problem.academic_year.id,
            academic_year_label=problem.academic_year.label,
            teacher_id=teacher.id,
            teacher_name=teacher.full_name,
            version_number=active.version_number,
            solver_status=active.solver_status,
            total_soft_penalty=active.total_soft_penalty,
            created_at=active.created_at,
            is_active=True,
            days=days,
            rows=rows,
        )


def _project_entry(
    entry: ScheduleEntry,
    activities_by_id: dict[str, str],
    groups_by_id: dict[str, ParticipantGroup],
    class_sections_by_id: dict[str, str],
) -> TeacherTimetableEntry:
    """Resolve display names/role via strict lookup -- a non-null
    referenced ID missing from `problem`'s own lookup tables is a
    genuine configuration inconsistency and must raise (`KeyError`),
    never silently serialize as a blank/`None` name or an inferred
    role. `participant_group_role`/`class_sections` are read straight
    off the authoritative `ParticipantGroup`, never derived from the
    group's name or `entry.class_sections`' own length."""
    group = None if entry.participant_group_id is None else groups_by_id[entry.participant_group_id]
    resolved_class_sections = tuple(
        TeacherTimetableClassSection(id=class_id, name=class_sections_by_id[class_id])
        for class_id in entry.class_sections
    )
    return TeacherTimetableEntry(
        source=entry.source,
        activity_id=entry.activity_id,
        activity_name=activities_by_id[entry.activity_id],
        participant_group_id=entry.participant_group_id,
        participant_group_name=None if group is None else group.name,
        participant_group_role=None if group is None else group.role.value,
        class_sections=resolved_class_sections,
        requirement_id=entry.requirement_id,
        reserved_block_id=entry.reserved_block_id,
        resource_id=entry.resource_id,
    )
