"""`CalendarProjectionService` (Calendar A): the read-only Calendar
projection backing `GET .../calendar` -- mirrors
`ResourceProjectionService`'s projection pattern exactly, kept entirely
separate from `CalendarService` (the write use case).

Depends only on the two existing application-owned repository ports
(`application.ports`) -- never SQLAlchemy, persistence concrete
adapters, ORM models, or FastAPI, even transitively.
"""
from __future__ import annotations

from school_timetable.application.ports import ConfigurationRevisionRepository, SchedulingProblemRepository
from school_timetable.application.calendar_projection_models import (
    CalendarDayItem,
    CalendarPeriodItem,
    CalendarProjectionView,
)
from school_timetable.domain.calendar import derive_starts_new_block


class CalendarProjectionService:
    """Depends only on the two existing repository ports -- never
    SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        configuration_revision_repository: ConfigurationRevisionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._configuration_revision_repository = configuration_revision_repository

    def project(self, school_natural_id: str, academic_year_natural_id: str) -> CalendarProjectionView:
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
        # `problem.days`/`problem.periods` are already loaded in `idx`
        # order (`problem_repository.py`); this never re-sorts.
        days = tuple(CalendarDayItem(id=d.id, name=d.name, index=d.index) for d in problem.days)

        markers = derive_starts_new_block(problem.periods)
        periods = tuple(
            CalendarPeriodItem(
                id=p.id, name=p.name, index=p.index, start_time=p.start_time, end_time=p.end_time,
                starts_new_block=markers[p.id], is_instructional=p.is_instructional,
            )
            for p in problem.periods
        )

        return CalendarProjectionView(configuration_locked=configuration_locked, days=days, periods=periods)
