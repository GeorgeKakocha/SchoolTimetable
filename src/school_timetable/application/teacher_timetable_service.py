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

from datetime import datetime

from school_timetable.application.errors import TeacherNotFoundError
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.teacher_timetable_models import (
    DayHeader,
    TeacherTimetableCell,
    TeacherTimetableRow,
    TeacherTimetableView,
)
from school_timetable.application.teacher_timetable_entry_projection import (
    project_teacher_timetable_entry,
)
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus


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
        # Load the active schedule first (Safe Configuration Changes,
        # Slice A: the active version's own configuration revision is
        # what this projection must resolve config from, never
        # "whatever is currently published/draft"). SchedulingProblemNotFoundError
        # propagates unchanged if the school/year itself does not
        # resolve. None means "no Schedule has been generated yet" -- an
        # ordinary, expected outcome, not an error.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if active is None:
            problem = self._problem_repository.load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            _resolve_teacher(problem, school_natural_id, academic_year_natural_id, teacher_id)
            return None

        problem = self._problem_repository.load_for_revision(
            school_natural_id, academic_year_natural_id, active.configuration_revision_number,
        )
        teacher = _resolve_teacher(problem, school_natural_id, academic_year_natural_id, teacher_id)

        return _build_view(
            problem, teacher, active.entries, active.version_number, active.solver_status,
            active.total_soft_penalty, active.created_at, is_active=True,
        )

    def project_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
        version_number: int,
    ) -> TeacherTimetableView | None:
        """Historical sibling of `project`: projects one SPECIFIC,
        possibly-inactive `ScheduleVersion` (schedule version history +
        restore slice) instead of always the current active one -- the
        exact same projection rules, never duplicated. Resolves
        `SchedulingProblem` from THIS version's own `configuration_
        revision_number`, never "whatever is currently published/draft".
        Returns `None` only if no `Schedule` exists at all yet for this
        school/year (mirrors `project`'s own convention); raises
        `school_timetable.application.errors.ScheduleVersionNotFoundError`
        if a `Schedule` exists but this `version_number` does not."""
        snapshot = self._schedule_repository.get_version(
            school_natural_id, academic_year_natural_id, version_number,
        )
        if snapshot is None:
            problem = self._problem_repository.load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            _resolve_teacher(problem, school_natural_id, academic_year_natural_id, teacher_id)
            return None

        problem = self._problem_repository.load_for_revision(
            school_natural_id, academic_year_natural_id, snapshot.configuration_revision_number,
        )
        teacher = _resolve_teacher(problem, school_natural_id, academic_year_natural_id, teacher_id)

        return _build_view(
            problem, teacher, snapshot.entries, snapshot.version_number, snapshot.solver_status,
            snapshot.total_soft_penalty, snapshot.created_at, is_active=snapshot.is_active,
        )


def _resolve_teacher(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    teacher_id: str,
) -> Teacher:
    """A caller-supplied bad teacher ID is a distinct, narrower "not
    found" than an unknown school/year."""
    teacher = next((t for t in problem.teachers if t.id == teacher_id), None)
    if teacher is None:
        raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_id)
    return teacher


def _build_view(
    problem: SchedulingProblem,
    teacher: Teacher,
    entries: tuple[ScheduleEntry, ...],
    version_number: int,
    solver_status: SolverStatus,
    total_soft_penalty: int,
    created_at: datetime,
    *,
    is_active: bool,
) -> TeacherTimetableView:
    """Builds the view model purely in memory -- no further DB access.
    Shared by `project`/`project_version` so the actual projection rules
    are never duplicated between the active and historical code paths."""
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
    for entry in entries:
        if entry.teacher_id != teacher.id:
            continue
        key = (entry.day_id, entry.period_id)
        cells.setdefault(key, []).append(
            project_teacher_timetable_entry(
                entry, activities_by_id, groups_by_id, class_sections_by_id,
            )
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
        version_number=version_number,
        solver_status=solver_status,
        total_soft_penalty=total_soft_penalty,
        created_at=created_at,
        is_active=is_active,
        days=days,
        rows=rows,
    )
