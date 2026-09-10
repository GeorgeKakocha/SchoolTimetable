"""`ResourceService` (Resources Slice A): the narrow Resource catalog
write use case -- create, update, and delete a `domain.resources.
Resource`.

Depends only on the three application-owned repository ports
(`application.ports`) plus the pure `resource_rules` module -- never
SQLAlchemy, persistence concrete adapters, ORM models, or FastAPI, even
transitively, matching `SpecialActivityService`'s own discipline.

Each public method: (1) normalizes and validates the input fields
against the just-loaded snapshot -- a fast, user-friendly failure path
that is explicitly NOT the concurrency guarantee; (2) calls the
`ResourceRepository` write port, passing the *same* pure validation
callable as `validate` -- the port re-invokes it, authoritatively,
against a freshly-reloaded snapshot taken *after* acquiring its
Owner-Decision-#36 row lock, so the two checks can never silently
diverge and a write is never committed against a stale snapshot.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import resource_rules as rules
from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.ports import (
    ResourceRepository,
    ScheduleVersionRepository,
    SchedulingProblemRepository,
)
from school_timetable.application.resource_models import ResourceFields, ResourceWriteResult
from school_timetable.domain.problem import SchedulingProblem


def _default_resource_id_factory() -> str:
    """Backend-generated, opaque, stable natural ID -- never a
    persistence surrogate PK, never client-supplied, never
    name-derived/slug. Named after the persisted/domain entity itself
    (`Resource`), mirroring `activity_`/`teacher_`/`class_`/
    `reserved_block_` convention exactly."""
    return f"resource_{uuid4().hex}"


class ResourceService:
    """The narrow Resources Slice A write use case -- shaped like
    `SpecialActivityService`, not a generic repository."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        resource_repository: ResourceRepository,
        schedule_repository: ScheduleVersionRepository,
        resource_id_factory: Callable[[], str] = _default_resource_id_factory,
    ) -> None:
        self._problem_repository = problem_repository
        self._resource_repository = resource_repository
        self._schedule_repository = schedule_repository
        self._resource_id_factory = resource_id_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        fields: ResourceFields,
    ) -> ResourceWriteResult:
        name = rules.normalize_name(fields.name)
        capacity = fields.capacity
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        resource_natural_id = self._resource_id_factory()

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_create(
                current_problem, school_natural_id, academic_year_natural_id, name=name, capacity=capacity,
            )

        # Fast precheck against the just-loaded snapshot -- the
        # authoritative recheck (identical pure logic) happens again
        # inside the port, under its lock, against a fresh reload.
        validate(problem)

        self._resource_repository.create(
            school_natural_id, academic_year_natural_id, resource_natural_id, name, capacity, validate=validate,
        )
        return ResourceWriteResult(id=resource_natural_id, name=name, capacity=capacity)

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_id: str,
        fields: ResourceFields,
    ) -> ResourceWriteResult:
        name = rules.normalize_name(fields.name)
        capacity = fields.capacity
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_update(
                current_problem, school_natural_id, academic_year_natural_id, resource_id,
                name=name, capacity=capacity,
            )

        validate(problem)

        self._resource_repository.update(
            school_natural_id, academic_year_natural_id, resource_id, name, capacity, validate=validate,
        )
        return ResourceWriteResult(id=resource_id, name=name, capacity=capacity)

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_id: str,
    ) -> None:
        self._precheck_not_locked(school_natural_id, academic_year_natural_id)

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        def validate(current_problem: SchedulingProblem) -> None:
            rules.validate_delete(current_problem, school_natural_id, academic_year_natural_id, resource_id)

        validate(problem)

        self._resource_repository.delete(
            school_natural_id, academic_year_natural_id, resource_id, validate=validate,
        )

    def _precheck_not_locked(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        # Fast, un-locked precheck only -- avoids unnecessary work for
        # the common case, but is explicitly NOT the concurrency
        # guarantee (Owner Decision #36): the authoritative recheck
        # happens inside the write port, under its `AcademicYear` lock.
        existing = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if existing is not None:
            raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)
