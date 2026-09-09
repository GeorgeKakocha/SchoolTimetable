"""`TeachingAssignmentsProjectionService` (Phase 3C.2b): the read-only
Teaching Assignments page projection -- mirrors `ClassTimetableService`'s
projection pattern exactly, kept entirely separate from
`TeachingAssignmentService` (the write use case) the same way
`ClassTimetableService` is kept separate from `GenerateScheduleService`.

Depends only on the two existing application-owned repository ports
(`application.ports`) plus the pure `teaching_assignment_rules.plain_reasons`
predicate -- never SQLAlchemy, persistence concrete adapters, ORM
models, or FastAPI, even transitively. No DB `Session`/connection is
held by this service itself: both repository calls finish before any of
this module's own, pure, in-memory projection logic runs.

Editability is never reimplemented here -- `plain_reasons` (Decision
#34/#36's one locked predicate, already reused by both the write
service's fast precheck and its lock-protected authoritative recheck)
is called directly for every requirement, so a `FixedPlacement`
reference (or any other advanced feature) affects `editable`/
`advanced_reasons` automatically, with zero API-local heuristics.
"""
from __future__ import annotations

from school_timetable.application import teaching_assignment_rules as rules
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.teaching_assignments_projection_models import (
    ActivityOption,
    TeacherOption,
    TeacherWorkload,
    TeachingAssignmentClassSection,
    TeachingAssignmentItem,
    TeachingAssignmentsProjectionView,
    WholeClassTarget,
)
from school_timetable.domain.groups import ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import TeachingRequirement


class TeachingAssignmentsProjectionService:
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
    ) -> TeachingAssignmentsProjectionView:
        # (1) Load config. SchedulingProblemNotFoundError propagates
        # unchanged if the school/year itself does not resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (2) Decision #35's existing gate, reused verbatim -- reads
        # remain available regardless of lock state; only writes reject.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        configuration_locked = active is not None

        # (3) Build the view model purely in memory -- no further DB access.
        teachers_by_id = {t.id: t.full_name for t in problem.teachers}
        activities_by_id = {a.id: a.name for a in problem.activities}
        groups_by_id = {g.id: g for g in problem.participant_groups}
        class_sections_by_id = {c.id: c.name for c in problem.class_sections}

        assignments = tuple(
            _project_assignment(r, problem, teachers_by_id, activities_by_id, groups_by_id, class_sections_by_id)
            for r in problem.teaching_requirements
        )

        teachers = tuple(TeacherOption(id=t.id, name=t.full_name) for t in problem.teachers)
        activities = tuple(ActivityOption(id=a.id, name=a.name) for a in problem.activities)

        whole_class_targets = tuple(
            target
            for target in (
                _whole_class_target(c.id, c.name, problem.participant_groups)
                for c in problem.class_sections
            )
            if target is not None
        )

        totals_by_teacher: dict[str, int] = {t.id: 0 for t in problem.teachers}
        for requirement in problem.teaching_requirements:
            if requirement.teacher_id in totals_by_teacher:
                totals_by_teacher[requirement.teacher_id] += requirement.weekly_periods
        teacher_workloads = tuple(
            TeacherWorkload(
                teacher_id=t.id, teacher_name=t.full_name, total_weekly_periods=totals_by_teacher[t.id],
            )
            for t in problem.teachers
        )

        return TeachingAssignmentsProjectionView(
            configuration_locked=configuration_locked,
            assignments=assignments,
            teachers=teachers,
            whole_class_targets=whole_class_targets,
            activities=activities,
            teacher_workloads=teacher_workloads,
        )


def _project_assignment(
    requirement: TeachingRequirement,
    problem: SchedulingProblem,
    teachers_by_id: dict[str, str],
    activities_by_id: dict[str, str],
    groups_by_id: dict[str, ParticipantGroup],
    class_sections_by_id: dict[str, str],
) -> TeachingAssignmentItem:
    """Resolve display names via strict lookup -- a non-null referenced
    ID missing from `problem`'s own lookup tables is a genuine
    configuration inconsistency and must raise (`KeyError`), never
    silently serialize as a blank/`None` name, matching
    `ClassTimetableService._project_entry`'s exact discipline."""
    group = groups_by_id[requirement.participant_group_id]
    reasons = rules.plain_reasons(problem, requirement)
    return TeachingAssignmentItem(
        id=requirement.id,
        teacher_id=requirement.teacher_id,
        teacher_name=teachers_by_id[requirement.teacher_id],
        activity_id=requirement.activity_id,
        activity_name=activities_by_id[requirement.activity_id],
        participant_group_id=group.id,
        participant_group_name=group.name,
        participant_group_role=group.role.value,
        class_sections=tuple(
            TeachingAssignmentClassSection(id=cid, name=class_sections_by_id[cid])
            for cid in group.class_sections
        ),
        weekly_periods=requirement.weekly_periods,
        editable=len(reasons) == 0,
        advanced_reasons=reasons,
    )


def _whole_class_target(
    class_section_id: str,
    class_section_name: str,
    participant_groups: tuple[ParticipantGroup, ...],
) -> WholeClassTarget | None:
    """The unique `WHOLE_CLASS` group whose sole `class_sections` member
    is `class_section_id`, or `None` if that Decision #33 invariant is
    broken (zero or more than one match) -- fail soft, never guess."""
    matches = [
        g for g in participant_groups
        if g.role == ParticipantGroupRole.WHOLE_CLASS and g.class_sections == (class_section_id,)
    ]
    if len(matches) != 1:
        return None
    group = matches[0]
    return WholeClassTarget(
        class_section_id=class_section_id,
        class_section_name=class_section_name,
        participant_group_id=group.id,
        participant_group_name=group.name,
    )
