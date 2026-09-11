"""`ClassTimetableService` (Phase 3B.1, `docs/DECISIONS.md` #32): the
first Phase 3B application service, projecting the active generated
schedule into one `ClassSection`'s ordered timetable grid.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, FastAPI, or API schemas, even transitively. No DB
`Session`/connection is held by this service itself: both repository
calls finish (open and close their own short session internally) before
any of this module's own, pure, in-memory projection logic runs. No
solver call, no verifier call, no repository write -- this is read-only
projection of already-generated, already-persisted, already-detached
data.

Membership, grouping, ordering, and name-resolution are all decided
here, once, in the backend -- never by a caller (eventually React) --
per Decision #32 Owner Decisions 1/3/7/8.
"""
from __future__ import annotations

from datetime import datetime

from school_timetable.application.class_timetable_models import (
    ClassTimetableCell,
    ClassTimetableEntry,
    ClassTimetableRow,
    ClassTimetableView,
    DayHeader,
)
from school_timetable.application.errors import ClassSectionNotFoundError
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.domain.groups import ClassSection
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus


class ClassTimetableService:
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
        class_section_id: str,
    ) -> ClassTimetableView | None:
        # (1) Load config. SchedulingProblemNotFoundError propagates
        # unchanged if the school/year itself does not resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        class_section = _resolve_class_section(
            problem, school_natural_id, academic_year_natural_id, class_section_id,
        )

        # Load the active schedule. None means "no Schedule has been
        # generated yet" -- an ordinary, expected outcome, not an error.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if active is None:
            return None

        return _build_view(
            problem, class_section, active.entries, active.version_number, active.solver_status,
            active.total_soft_penalty, active.created_at, is_active=True,
        )

    def project_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_section_id: str,
        version_number: int,
    ) -> ClassTimetableView | None:
        """Historical sibling of `project`: projects one SPECIFIC,
        possibly-inactive `ScheduleVersion` (schedule version history +
        restore slice) instead of always the current active one -- the
        exact same projection rules (membership, grouping, ordering,
        name resolution), never duplicated. Returns `None` only if no
        `Schedule` exists at all yet for this school/year (mirrors
        `project`'s own convention); raises `school_timetable.
        application.errors.ScheduleVersionNotFoundError` if a `Schedule`
        exists but this `version_number` does not."""
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        class_section = _resolve_class_section(
            problem, school_natural_id, academic_year_natural_id, class_section_id,
        )

        snapshot = self._schedule_repository.get_version(
            school_natural_id, academic_year_natural_id, version_number,
        )
        if snapshot is None:
            return None

        return _build_view(
            problem, class_section, snapshot.entries, snapshot.version_number, snapshot.solver_status,
            snapshot.total_soft_penalty, snapshot.created_at, is_active=snapshot.is_active,
        )


def _resolve_class_section(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    class_section_id: str,
) -> ClassSection:
    """A caller-supplied bad class ID is a distinct, narrower "not
    found" than an unknown school/year."""
    class_section = next((c for c in problem.class_sections if c.id == class_section_id), None)
    if class_section is None:
        raise ClassSectionNotFoundError(school_natural_id, academic_year_natural_id, class_section_id)
    return class_section


def _build_view(
    problem: SchedulingProblem,
    class_section: ClassSection,
    entries: tuple[ScheduleEntry, ...],
    version_number: int,
    solver_status: SolverStatus,
    total_soft_penalty: int,
    created_at: datetime,
    *,
    is_active: bool,
) -> ClassTimetableView:
    """Builds the view model purely in memory -- no further DB access.
    Shared by `project`/`project_version` so the actual projection rules
    (membership, grouping, ordering, name resolution) are never
    duplicated between the active and historical code paths."""
    days = tuple(
        DayHeader(id=d.id, name=d.name) for d in sorted(problem.days, key=lambda d: d.index)
    )
    periods = sorted(
        (p for p in problem.periods if p.is_instructional), key=lambda p: p.index,
    )

    activities_by_id = {a.id: a.name for a in problem.activities}
    teachers_by_id = {t.id: t.full_name for t in problem.teachers}
    groups_by_id = {g.id: g.name for g in problem.participant_groups}

    cells: dict[tuple[str, str], list[ClassTimetableEntry]] = {}
    for entry in entries:
        if class_section.id not in entry.class_sections:
            continue
        key = (entry.day_id, entry.period_id)
        cells.setdefault(key, []).append(
            _project_entry(entry, activities_by_id, teachers_by_id, groups_by_id)
        )

    rows = tuple(
        ClassTimetableRow(
            period_id=period.id,
            period_name=period.name,
            cells=tuple(
                ClassTimetableCell(
                    day_id=day.id,
                    entries=tuple(cells.get((day.id, period.id), ())),
                )
                for day in days
            ),
        )
        for period in periods
    )

    return ClassTimetableView(
        school_id=problem.school.id,
        school_name=problem.school.name,
        academic_year_id=problem.academic_year.id,
        academic_year_label=problem.academic_year.label,
        class_section_id=class_section.id,
        class_section_name=class_section.name,
        version_number=version_number,
        solver_status=solver_status,
        total_soft_penalty=total_soft_penalty,
        created_at=created_at,
        is_active=is_active,
        days=days,
        rows=rows,
    )


def _project_entry(
    entry: ScheduleEntry,
    activities_by_id: dict[str, str],
    teachers_by_id: dict[str, str],
    groups_by_id: dict[str, str],
) -> ClassTimetableEntry:
    """Resolve display names via strict lookup -- a non-null referenced
    ID missing from `problem`'s own lookup tables is a genuine
    configuration inconsistency and must raise (`KeyError`), never
    silently serialize as a blank/`None` name."""
    return ClassTimetableEntry(
        source=entry.source,
        activity_id=entry.activity_id,
        activity_name=activities_by_id[entry.activity_id],
        teacher_id=entry.teacher_id,
        teacher_name=None if entry.teacher_id is None else teachers_by_id[entry.teacher_id],
        participant_group_id=entry.participant_group_id,
        participant_group_name=(
            None if entry.participant_group_id is None else groups_by_id[entry.participant_group_id]
        ),
        requirement_id=entry.requirement_id,
        reserved_block_id=entry.reserved_block_id,
        resource_id=entry.resource_id,
    )
