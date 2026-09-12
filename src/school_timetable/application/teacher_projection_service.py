"""`TeacherProjectionService` (Real-School Setup MVP Slice B): the
read-only Teachers page projection -- mirrors
`TeachingAssignmentsProjectionService`'s projection pattern exactly,
kept entirely separate from `TeacherService` (the write use case) the
same way `TeachingAssignmentsProjectionService` is kept separate from
`TeachingAssignmentService`.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself: both repository calls
finish before any of this module's own, pure, in-memory projection
logic runs.
"""
from __future__ import annotations

from school_timetable.application.ports import ConfigurationRevisionRepository, SchedulingProblemRepository
from school_timetable.application.teacher_projection_models import (
    TeacherProjectionItem,
    TeachersProjectionView,
)


class TeacherProjectionService:
    """Depends only on the two existing repository ports -- never
    SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        configuration_revision_repository: ConfigurationRevisionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._configuration_revision_repository = configuration_revision_repository

    def project(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> TeachersProjectionView:
        # (1) Load config. SchedulingProblemNotFoundError propagates
        # unchanged if the school/year itself does not resolve.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (2) Safe Configuration Changes, Slice B: `configuration_locked`
        # is `True` exactly when no draft is currently open -- reads
        # remain available regardless of lock state; only writes reject.
        configuration_locked = self._configuration_revision_repository.get_state(
            school_natural_id, academic_year_natural_id,
        ).configuration_locked

        # (3) Build the view purely in memory -- no further DB access.
        # `problem.teachers`' own order is already persistence ordinal
        # order (`problem_repository.py`); no re-sorting here.
        teachers = tuple(
            TeacherProjectionItem(
                id=t.id, first_name=t.first_name, last_name=t.last_name, name=t.full_name,
            )
            for t in problem.teachers
        )

        return TeachersProjectionView(configuration_locked=configuration_locked, teachers=teachers)
