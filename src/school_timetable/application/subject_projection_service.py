"""`SubjectProjectionService` (Real-School Setup MVP Slice D): the
read-only Subjects page projection -- mirrors
`ClassSectionProjectionService`'s projection pattern exactly, kept
entirely separate from `SubjectService` (the write use case) the same
way `ClassSectionProjectionService` is kept separate from
`ClassSectionService`. Deliberately its own, independently-owned
projection -- never a shortcut reuse of
`TeachingAssignmentsProjectionService`'s own (already `ORDINARY`-only,
since the pre-Slice-D correction) `activities` list, which exists for a
different surface with a different response shape.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself: both repository calls
finish before any of this module's own, pure, in-memory projection
logic runs.
"""
from __future__ import annotations

from school_timetable.application.ports import ConfigurationRevisionRepository, SchedulingProblemRepository
from school_timetable.application.subject_projection_models import (
    SubjectProjectionItem,
    SubjectsProjectionView,
)
from school_timetable.domain.activities import ActivityKind


class SubjectProjectionService:
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
    ) -> SubjectsProjectionView:
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
        # `problem.activities`' own order is already persistence
        # ordinal order (`problem_repository.py`); filtering to
        # ORDINARY never re-sorts.
        subjects = tuple(
            SubjectProjectionItem(id=a.id, name=a.name)
            for a in problem.activities
            if a.kind == ActivityKind.ORDINARY
        )

        return SubjectsProjectionView(configuration_locked=configuration_locked, subjects=subjects)
