"""PostgreSQL adapter for the atomic synchronized two-branch split write."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand,
    SynchronizedSplitPublicIds,
)
from school_timetable.domain.groups import ParticipantGroupRole
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_draft_revision_id,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


class SqlAlchemySynchronizedSplitRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        command: CreateSynchronizedSplitCommand,
        public_ids: SynchronizedSplitPublicIds,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, command.school_id, command.academic_year_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, command.school_id, command.academic_year_id)
            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                command.school_id, command.academic_year_id,
            )
            validate(current_problem)
            revision_id = resolve_draft_revision_id(
                session, year_id, command.school_id, command.academic_year_id,
            )
            class_id = _natural_id(session, orm.ClassSection, year_id, revision_id, command.class_section_id)
            teacher_ids = {
                branch.teacher_id: _natural_id(session, orm.Teacher, year_id, revision_id, branch.teacher_id)
                for branch in (command.branch_a, command.branch_b)
            }
            activity_ids = {
                branch.activity_id: _natural_id(session, orm.Activity, year_id, revision_id, branch.activity_id)
                for branch in (command.branch_a, command.branch_b)
            }
            group_ordinal = _next_ordinal(session, orm.ParticipantGroup, year_id, revision_id)
            requirement_ordinal = _next_ordinal(session, orm.TeachingRequirement, year_id, revision_id)

            group_rows = []
            for offset, (branch, group_public_id) in enumerate((
                (command.branch_a, public_ids.branch_a_group_id),
                (command.branch_b, public_ids.branch_b_group_id),
            )):
                group = orm.ParticipantGroup(
                    academic_year_id=year_id, configuration_revision_id=revision_id,
                    natural_id=group_public_id, name=branch.participant_group_name,
                    role=ParticipantGroupRole.SUBGROUP.value, ordinal=group_ordinal + offset,
                )
                session.add(group)
                session.flush()
                session.add(orm.ParticipantGroupClassSection(
                    academic_year_id=year_id, configuration_revision_id=revision_id,
                    participant_group_id=group.id, class_section_id=class_id, ordinal=0,
                ))
                group_rows.append(group)

            for offset, (branch, group, requirement_public_id) in enumerate((
                (command.branch_a, group_rows[0], public_ids.branch_a_requirement_id),
                (command.branch_b, group_rows[1], public_ids.branch_b_requirement_id),
            )):
                session.add(orm.TeachingRequirement(
                    academic_year_id=year_id, configuration_revision_id=revision_id,
                    natural_id=requirement_public_id,
                    teacher_id=teacher_ids[branch.teacher_id], activity_id=activity_ids[branch.activity_id],
                    participant_group_id=group.id, weekly_periods=command.weekly_periods,
                    block_mode=BlockPolicyMode.FLEXIBLE.value, block_sizes=[],
                    min_distinct_days=None, max_periods_per_day=None, resource_id=None,
                    split_group_id=public_ids.split_group_id, ordinal=requirement_ordinal + offset,
                ))
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _natural_id(session: Session, model: type, year_id: int, revision_id: int, natural_id: str) -> int:
    return session.execute(select(model.id).where(
        model.academic_year_id == year_id,
        model.configuration_revision_id == revision_id,
        model.natural_id == natural_id,
    )).scalar_one()


def _next_ordinal(session: Session, model: type, year_id: int, revision_id: int) -> int:
    value = session.execute(select(func.max(model.ordinal)).where(
        model.academic_year_id == year_id,
        model.configuration_revision_id == revision_id,
    )).scalar()
    return 0 if value is None else value + 1
