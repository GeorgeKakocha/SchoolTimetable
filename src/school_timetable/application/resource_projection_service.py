"""`ResourceProjectionService` (Resources Slice A): the read-only
Resources catalog projection -- mirrors
`SpecialActivityProjectionService`'s projection pattern exactly, kept
entirely separate from `ResourceService` (the write use case).

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself: both repository calls
finish before any of this module's own, pure, in-memory projection
logic runs.
"""
from __future__ import annotations

from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.resource_projection_models import (
    ResourceProjectionItem,
    ResourcesProjectionView,
)


class ResourceProjectionService:
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
    ) -> ResourcesProjectionView:
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

        # (3) Build the view purely in memory -- no further DB access.
        # `problem.resources`' own order is already persistence ordinal
        # order (`problem_repository.py`); this never re-sorts.
        resources = tuple(
            ResourceProjectionItem(id=r.id, name=r.name, capacity=r.capacity) for r in problem.resources
        )

        return ResourcesProjectionView(configuration_locked=configuration_locked, resources=resources)
