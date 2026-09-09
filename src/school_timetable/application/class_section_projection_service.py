"""`ClassSectionProjectionService` (Real-School Setup MVP Slice C): the
read-only Classes page projection -- mirrors
`TeacherProjectionService`'s projection pattern exactly, kept entirely
separate from `ClassSectionService` (the write use case) the same way
`TeacherProjectionService` is kept separate from `TeacherService`.

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively. No DB `Session`/
connection is held by this service itself: both repository calls
finish before any of this module's own, pure, in-memory projection
logic runs.
"""
from __future__ import annotations

from school_timetable.application.class_section_projection_models import (
    ClassSectionProjectionItem,
    ClassSectionsProjectionView,
)
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository


class ClassSectionProjectionService:
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
    ) -> ClassSectionsProjectionView:
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
        # `problem.class_sections`' own order is already persistence
        # ordinal order (`problem_repository.py`); no re-sorting here.
        classes = tuple(
            ClassSectionProjectionItem(id=c.id, name=c.name) for c in problem.class_sections
        )

        return ClassSectionsProjectionView(configuration_locked=configuration_locked, classes=classes)
