"""Application orchestration for an atomic synchronized two-branch split."""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from school_timetable.application import synchronized_split_rules as rules
from school_timetable.application.ports import SchedulingProblemRepository, SynchronizedSplitRepository
from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand, SynchronizedSplitBranchResult,
    SynchronizedSplitPublicIds, SynchronizedSplitResult,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError


def _default_id_factory(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SynchronizedSplitService:
    def __init__(self, problem_repository: SchedulingProblemRepository,
                 synchronized_split_repository: SynchronizedSplitRepository,
                 id_factory: Callable[[str], str] = _default_id_factory) -> None:
        self._problem_repository = problem_repository
        self._repository = synchronized_split_repository
        self._id_factory = id_factory

    def create(self, command: CreateSynchronizedSplitCommand) -> SynchronizedSplitResult:
        command = rules.normalize_command(command)
        public_ids = SynchronizedSplitPublicIds(
            self._id_factory("split"), self._id_factory("group"), self._id_factory("group"),
            self._id_factory("req"), self._id_factory("req"),
        )
        problem = self._problem_repository.load_by_school_and_year(command.school_id, command.academic_year_id)
        warnings_holder: list[tuple[ValidationError, ...]] = [()]

        def validate(current_problem: SchedulingProblem) -> None:
            warnings_holder[0] = rules.validate_create(current_problem, command, public_ids)

        validate(problem)
        self._repository.create(command, public_ids, validate)
        return SynchronizedSplitResult(
            public_ids.split_group_id, command.class_section_id, command.weekly_periods,
            SynchronizedSplitBranchResult(
                public_ids.branch_a_group_id, command.branch_a.participant_group_name,
                command.branch_a.teacher_id, command.branch_a.activity_id,
                public_ids.branch_a_requirement_id,
            ),
            SynchronizedSplitBranchResult(
                public_ids.branch_b_group_id, command.branch_b.participant_group_name,
                command.branch_b.teacher_id, command.branch_b.activity_id,
                public_ids.branch_b_requirement_id,
            ),
            warnings_holder[0],
        )
