"""Pure application projection for the whole-school Teacher Matrix."""
from __future__ import annotations

from datetime import datetime

from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.teacher_timetable_entry_projection import (
    project_teacher_timetable_entry,
)
from school_timetable.application.teacher_timetable_models import TeacherTimetableEntry
from school_timetable.application.teacher_timetable_matrix_models import (
    TeacherMatrixCell,
    TeacherMatrixDay,
    TeacherMatrixPeriod,
    TeacherMatrixTeacher,
    TeacherTimetableMatrixView,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus


class TeacherTimetableMatrixService:
    """Projects one immutable schedule version into a whole-school matrix."""

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
    ) -> TeacherTimetableMatrixView | None:
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if active is None:
            return None
        problem = self._problem_repository.load_for_revision(
            school_natural_id,
            academic_year_natural_id,
            active.configuration_revision_number,
        )
        return _build_view(
            problem,
            active.entries,
            active.version_number,
            active.solver_status,
            active.total_soft_penalty,
            active.created_at,
            is_active=True,
        )

    def project_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        version_number: int,
    ) -> TeacherTimetableMatrixView | None:
        snapshot = self._schedule_repository.get_version(
            school_natural_id, academic_year_natural_id, version_number,
        )
        if snapshot is None:
            return None
        problem = self._problem_repository.load_for_revision(
            school_natural_id,
            academic_year_natural_id,
            snapshot.configuration_revision_number,
        )
        return _build_view(
            problem,
            snapshot.entries,
            snapshot.version_number,
            snapshot.solver_status,
            snapshot.total_soft_penalty,
            snapshot.created_at,
            is_active=snapshot.is_active,
        )


def _build_view(
    problem: SchedulingProblem,
    entries: tuple[ScheduleEntry, ...],
    version_number: int,
    solver_status: SolverStatus,
    total_soft_penalty: int,
    created_at: datetime,
    *,
    is_active: bool,
) -> TeacherTimetableMatrixView:
    ordered_days = tuple(sorted(problem.days, key=lambda day: day.index))
    ordered_periods = tuple(sorted(
        (period for period in problem.periods if period.is_instructional),
        key=lambda period: period.index,
    ))
    day_ids = {day.id for day in ordered_days}
    period_ids = {period.id for period in ordered_periods}

    activities_by_id = {activity.id: activity.name for activity in problem.activities}
    groups_by_id = {group.id: group for group in problem.participant_groups}
    class_sections_by_id = {
        class_section.id: class_section.name for class_section in problem.class_sections
    }
    occupied: dict[str, dict[tuple[str, str], list[TeacherTimetableEntry]]] = {
        teacher.id: {} for teacher in problem.teachers
    }

    # The schedule-entry tuple is traversed exactly once.  Direct indexing
    # intentionally raises for an entry whose teacher is absent from this
    # version's configuration rather than silently hiding corrupt data.
    for entry in entries:
        if entry.teacher_id is None:
            continue
        if entry.day_id not in day_ids or entry.period_id not in period_ids:
            continue
        coordinate = (entry.day_id, entry.period_id)
        occupied[entry.teacher_id].setdefault(coordinate, []).append(
            project_teacher_timetable_entry(
                entry, activities_by_id, groups_by_id, class_sections_by_id,
            )
        )

    teachers = tuple(
        TeacherMatrixTeacher(
            id=teacher.id,
            name=teacher.full_name,
            cells=tuple(
                TeacherMatrixCell(
                    day_id=day.id,
                    period_id=period.id,
                    entries=tuple(occupied[teacher.id][(day.id, period.id)]),
                )
                for day in ordered_days
                for period in ordered_periods
                if (day.id, period.id) in occupied[teacher.id]
            ),
        )
        for teacher in problem.teachers
    )

    return TeacherTimetableMatrixView(
        school_id=problem.school.id,
        school_name=problem.school.name,
        academic_year_id=problem.academic_year.id,
        academic_year_label=problem.academic_year.label,
        version_number=version_number,
        solver_status=solver_status,
        total_soft_penalty=total_soft_penalty,
        created_at=created_at,
        is_active=is_active,
        days=tuple(TeacherMatrixDay(id=day.id, name=day.name) for day in ordered_days),
        periods=tuple(
            TeacherMatrixPeriod(id=period.id, name=period.name) for period in ordered_periods
        ),
        teachers=teachers,
    )
