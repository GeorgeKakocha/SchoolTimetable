"""`SpecialActivityProjectionService` (Reserved Activities Slice A1):
the read-only Special Activities catalog projection -- mirrors
`SubjectProjectionService`'s projection pattern exactly, kept entirely
separate from `SpecialActivityService` (the write use case) the same
way `SubjectProjectionService` is kept separate from `SubjectService`.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself: both repository calls
finish before any of this module's own, pure, in-memory projection
logic runs.
"""
from __future__ import annotations

from school_timetable.application.ports import ConfigurationRevisionRepository, SchedulingProblemRepository
from school_timetable.application.special_activity_projection_models import (
    SpecialActivitiesProjectionView,
    SpecialActivityProjectionItem,
)
from school_timetable.domain.activities import ActivityKind


class SpecialActivityProjectionService:
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
    ) -> SpecialActivitiesProjectionView:
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
        # ordinal order (`problem_repository.py`); filtering to CLUB
        # never re-sorts.
        special_activities = tuple(
            SpecialActivityProjectionItem(id=a.id, name=a.name)
            for a in problem.activities
            if a.kind == ActivityKind.CLUB
        )

        return SpecialActivitiesProjectionView(
            configuration_locked=configuration_locked, special_activities=special_activities,
        )
