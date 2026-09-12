"""add configuration revision foundation

Revision ID: 83434054f9d2
Revises: e0f73eda567b
Create Date: 2026-09-11 22:27:21.935539

Safe Configuration Changes, Slice A: introduces `configuration_revision`
as a first-class table, gives every AcademicYear a `published_revision_id`/
`draft_revision_id`, gives every one of the fifteen `SchedulingProblem`
configuration tables a `configuration_revision_id` (widening every
composite FK between two of them to match), and gives `schedule_version`
its own `configuration_revision_id`.

For every EXISTING `AcademicYear` this creates exactly one
`ConfigurationRevision` (`revision_number=1`) and backfills every
existing configuration row and every existing `ScheduleVersion` to it --
`PUBLISHED` (and `academic_year.published_revision_id` set) for a year
that already has a generated `Schedule`, `DRAFT` (and
`academic_year.draft_revision_id` set) for a year that does not. This is
an EXACT backfill, not an approximation: configuration writes have been
locked outright once a `Schedule` exists (Owner Decision #35) since
before this migration, so there has only ever been one true
configuration state per year throughout every existing row's history.
Nothing is rewritten, regenerated, or deleted.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83434054f9d2'
down_revision: Union[str, Sequence[str], None] = 'e0f73eda567b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every SchedulingProblemRepository-loaded configuration table -- exactly
# the fifteen models `problem_repository.py` scopes by `academic_year_id`
# today, in the same order that file lists them.
_CONFIG_TABLES = [
    "day", "period", "class_section", "teacher", "activity", "resource",
    "participant_group", "teaching_requirement", "teacher_availability",
    "reserved_block", "fixed_placement", "participant_group_class_section",
    "time_preference", "reserved_block_class_section", "reserved_block_slot",
]


def upgrade() -> None:
    # -- 1. The new table itself, plus its two partial-unique invariants
    # (at most one DRAFT, at most one PUBLISHED revision per year) --
    # created before any row exists, so they're enforced from the very
    # first insert this migration's own backfill performs below.
    op.create_table(
        'configuration_revision',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('academic_year_id', sa.BigInteger(), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED')", name='ck_configuration_revision_status'),
        sa.ForeignKeyConstraint(
            ['academic_year_id'], ['academic_year.id'],
            name='fk_configuration_revision_academic_year', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('academic_year_id', 'id', name='uq_configuration_revision_ay_id'),
        sa.UniqueConstraint('academic_year_id', 'revision_number', name='uq_configuration_revision_ay_number'),
    )
    op.create_index(
        'uq_configuration_revision_one_draft', 'configuration_revision', ['academic_year_id'],
        unique=True, postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.create_index(
        'uq_configuration_revision_one_published', 'configuration_revision', ['academic_year_id'],
        unique=True, postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    # -- 2. AcademicYear's two pointer columns -- nullable now, backfilled
    # below, left nullable afterward too (a year with no Schedule yet has
    # no published revision; `draft_revision_id` is null once published).
    op.add_column('academic_year', sa.Column('published_revision_id', sa.BigInteger(), nullable=True))
    op.add_column('academic_year', sa.Column('draft_revision_id', sa.BigInteger(), nullable=True))

    # -- 3. Every config table's own `configuration_revision_id` --
    # nullable for now; backfilled in step 5, tightened in step 6.
    for table in _CONFIG_TABLES:
        op.add_column(table, sa.Column('configuration_revision_id', sa.BigInteger(), nullable=True))
    op.add_column('schedule_version', sa.Column('configuration_revision_id', sa.BigInteger(), nullable=True))

    # -- 4. Exactly one ConfigurationRevision per existing AcademicYear:
    # PUBLISHED for a year that already has a generated Schedule (an
    # exact backfill -- configuration has been write-locked since before
    # this migration, so today's live config truly is what every
    # existing ScheduleVersion was generated/edited against), DRAFT for
    # a year that does not (its configuration is still, and always was,
    # freely editable).
    op.execute(
        "INSERT INTO configuration_revision (academic_year_id, revision_number, status) "
        "SELECT ay.id, 1, 'PUBLISHED' FROM academic_year ay "
        "WHERE EXISTS (SELECT 1 FROM schedule s WHERE s.academic_year_id = ay.id)"
    )
    op.execute(
        "INSERT INTO configuration_revision (academic_year_id, revision_number, status) "
        "SELECT ay.id, 1, 'DRAFT' FROM academic_year ay "
        "WHERE NOT EXISTS (SELECT 1 FROM schedule s WHERE s.academic_year_id = ay.id)"
    )

    # -- 5. Point every AcademicYear at its new revision, then backfill
    # every configuration row and every ScheduleVersion to the same
    # revision (there is exactly one per year at this point, so this
    # join is never ambiguous).
    op.execute(
        "UPDATE academic_year ay SET published_revision_id = cr.id "
        "FROM configuration_revision cr "
        "WHERE cr.academic_year_id = ay.id AND cr.status = 'PUBLISHED'"
    )
    op.execute(
        "UPDATE academic_year ay SET draft_revision_id = cr.id "
        "FROM configuration_revision cr "
        "WHERE cr.academic_year_id = ay.id AND cr.status = 'DRAFT'"
    )
    for table in _CONFIG_TABLES:
        op.execute(
            f"UPDATE {table} t SET configuration_revision_id = cr.id "
            "FROM configuration_revision cr "
            f"WHERE cr.academic_year_id = t.academic_year_id"
        )
    op.execute(
        "UPDATE schedule_version sv SET configuration_revision_id = cr.id "
        "FROM configuration_revision cr "
        "WHERE cr.academic_year_id = sv.academic_year_id"
    )

    # -- 6. Every config table's own `configuration_revision_id` is now
    # fully backfilled -- tighten to NOT NULL (schedule_version's own
    # too; academic_year's two pointers stay nullable, see step 2).
    for table in _CONFIG_TABLES:
        op.alter_column(table, 'configuration_revision_id', nullable=False)
    op.alter_column('schedule_version', 'configuration_revision_id', nullable=False)

    # -- 7. The two circular AcademicYear <-> ConfigurationRevision FKs
    # (mirrors `fk_schedule_active_version`'s own established
    # `use_alter=True` pattern for exactly this kind of two-table cycle).
    op.create_foreign_key(
        'fk_academic_year_published_revision', 'academic_year', 'configuration_revision',
        ['id', 'published_revision_id'], ['academic_year_id', 'id'], use_alter=True,
    )
    op.create_foreign_key(
        'fk_academic_year_draft_revision', 'academic_year', 'configuration_revision',
        ['id', 'draft_revision_id'], ['academic_year_id', 'id'], use_alter=True,
    )

    # -- 8. `schedule_version`'s own new FK -- no widening needed
    # elsewhere on the schedule side: `schedule_entry`/`locked_occurrence`
    # keep referencing `day`/`period`/`teaching_requirement`/
    # `reserved_block` exactly as before, via those tables' pre-existing,
    # untouched `(academic_year_id, id)` unique constraints.
    op.create_foreign_key(
        'fk_schedule_version_configuration_revision', 'schedule_version', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )

    # -- 9. Widen every natural_id/ordinal uniqueness and every
    # composite FK between two configuration tables to include
    # `configuration_revision_id`, and add each table's own FK back to
    # `configuration_revision`. The pre-existing 2-column
    # `(academic_year_id, id)` unique constraint on every table is left
    # in place throughout (never dropped) -- it still serves as the
    # unmodified target for `schedule_entry`/`locked_occurrence`'s own
    # references to `day`/`period`/`teaching_requirement`/
    # `reserved_block`, which this migration deliberately does not touch.
    #
    # Ordered by dependency, not alphabetically: a table referenced by
    # another config table via composite FK must have its own new
    # 3-column `(academic_year_id, configuration_revision_id, id)`
    # unique constraint created before any OTHER table's FK can be
    # widened to target it. Day/Period/ClassSection/Teacher/Activity/
    # Resource/ParticipantGroup first (referenced by others, reference
    # nothing themselves); then TeachingRequirement/ReservedBlock
    # (reference the first group, are themselves referenced by the
    # remaining leaf tables); then every pure-leaf table last.

    op.drop_constraint(op.f('uq_day_ay_idx'), 'day', type_='unique')
    op.create_unique_constraint(
        'uq_day_ay_idx', 'day', ['academic_year_id', 'configuration_revision_id', 'idx'],
    )
    op.drop_constraint(op.f('uq_day_ay_natural_id'), 'day', type_='unique')
    op.create_unique_constraint(
        'uq_day_ay_natural_id', 'day', ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.create_unique_constraint(
        'uq_day_ay_crid_id', 'day', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_day_configuration_revision', 'day', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_index(op.f('ix_period_ay_block'), table_name='period')
    op.create_index(
        'ix_period_ay_block', 'period', ['academic_year_id', 'configuration_revision_id', 'block_id'],
        unique=False,
    )
    op.drop_constraint(op.f('uq_period_ay_idx'), 'period', type_='unique')
    op.create_unique_constraint(
        'uq_period_ay_idx', 'period', ['academic_year_id', 'configuration_revision_id', 'idx'],
    )
    op.drop_constraint(op.f('uq_period_ay_natural_id'), 'period', type_='unique')
    op.create_unique_constraint(
        'uq_period_ay_natural_id', 'period', ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.create_unique_constraint(
        'uq_period_ay_crid_id', 'period', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_period_configuration_revision', 'period', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_class_section_ay_natural_id'), 'class_section', type_='unique')
    op.create_unique_constraint(
        'uq_class_section_ay_natural_id', 'class_section',
        ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_class_section_ay_ordinal'), 'class_section', type_='unique')
    op.create_unique_constraint(
        'uq_class_section_ay_ordinal', 'class_section',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_class_section_ay_crid_id', 'class_section', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_class_section_configuration_revision', 'class_section', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_teacher_ay_natural_id'), 'teacher', type_='unique')
    op.create_unique_constraint(
        'uq_teacher_ay_natural_id', 'teacher', ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_teacher_ay_ordinal'), 'teacher', type_='unique')
    op.create_unique_constraint(
        'uq_teacher_ay_ordinal', 'teacher', ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_teacher_ay_crid_id', 'teacher', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_teacher_configuration_revision', 'teacher', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_activity_ay_natural_id'), 'activity', type_='unique')
    op.create_unique_constraint(
        'uq_activity_ay_natural_id', 'activity', ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_activity_ay_ordinal'), 'activity', type_='unique')
    op.create_unique_constraint(
        'uq_activity_ay_ordinal', 'activity', ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_activity_ay_crid_id', 'activity', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_activity_configuration_revision', 'activity', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_resource_ay_natural_id'), 'resource', type_='unique')
    op.create_unique_constraint(
        'uq_resource_ay_natural_id', 'resource', ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_resource_ay_ordinal'), 'resource', type_='unique')
    op.create_unique_constraint(
        'uq_resource_ay_ordinal', 'resource', ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_resource_ay_crid_id', 'resource', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_resource_configuration_revision', 'resource', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_participant_group_ay_natural_id'), 'participant_group', type_='unique')
    op.create_unique_constraint(
        'uq_participant_group_ay_natural_id', 'participant_group',
        ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_participant_group_ay_ordinal'), 'participant_group', type_='unique')
    op.create_unique_constraint(
        'uq_participant_group_ay_ordinal', 'participant_group',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_participant_group_ay_crid_id', 'participant_group',
        ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.create_foreign_key(
        'fk_participant_group_configuration_revision', 'participant_group', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_teaching_requirement_ay_natural_id'), 'teaching_requirement', type_='unique')
    op.create_unique_constraint(
        'uq_teaching_requirement_ay_natural_id', 'teaching_requirement',
        ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_teaching_requirement_ay_ordinal'), 'teaching_requirement', type_='unique')
    op.create_unique_constraint(
        'uq_teaching_requirement_ay_ordinal', 'teaching_requirement',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_teaching_requirement_ay_crid_id', 'teaching_requirement',
        ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.drop_constraint(op.f('fk_teaching_requirement_teacher'), 'teaching_requirement', type_='foreignkey')
    op.drop_constraint(op.f('fk_teaching_requirement_resource'), 'teaching_requirement', type_='foreignkey')
    op.drop_constraint(op.f('fk_teaching_requirement_participant_group'), 'teaching_requirement', type_='foreignkey')
    op.drop_constraint(op.f('fk_teaching_requirement_activity'), 'teaching_requirement', type_='foreignkey')
    op.create_foreign_key(
        'fk_teaching_requirement_teacher', 'teaching_requirement', 'teacher',
        ['academic_year_id', 'configuration_revision_id', 'teacher_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_teaching_requirement_configuration_revision', 'teaching_requirement', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_teaching_requirement_activity', 'teaching_requirement', 'activity',
        ['academic_year_id', 'configuration_revision_id', 'activity_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_teaching_requirement_participant_group', 'teaching_requirement', 'participant_group',
        ['academic_year_id', 'configuration_revision_id', 'participant_group_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_teaching_requirement_resource', 'teaching_requirement', 'resource',
        ['academic_year_id', 'configuration_revision_id', 'resource_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )

    op.drop_constraint(op.f('uq_reserved_block_ay_natural_id'), 'reserved_block', type_='unique')
    op.create_unique_constraint(
        'uq_reserved_block_ay_natural_id', 'reserved_block',
        ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_reserved_block_ay_ordinal'), 'reserved_block', type_='unique')
    op.create_unique_constraint(
        'uq_reserved_block_ay_ordinal', 'reserved_block',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.create_unique_constraint(
        'uq_reserved_block_ay_crid_id', 'reserved_block', ['academic_year_id', 'configuration_revision_id', 'id'],
    )
    op.drop_constraint(op.f('fk_reserved_block_activity'), 'reserved_block', type_='foreignkey')
    op.drop_constraint(op.f('fk_reserved_block_resource'), 'reserved_block', type_='foreignkey')
    op.drop_constraint(op.f('fk_reserved_block_teacher'), 'reserved_block', type_='foreignkey')
    op.create_foreign_key(
        'fk_reserved_block_activity', 'reserved_block', 'activity',
        ['academic_year_id', 'configuration_revision_id', 'activity_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_reserved_block_teacher', 'reserved_block', 'teacher',
        ['academic_year_id', 'configuration_revision_id', 'teacher_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_reserved_block_configuration_revision', 'reserved_block', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_reserved_block_resource', 'reserved_block', 'resource',
        ['academic_year_id', 'configuration_revision_id', 'resource_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )

    op.drop_constraint(op.f('fk_pgcs_class_section'), 'participant_group_class_section', type_='foreignkey')
    op.drop_constraint(op.f('fk_pgcs_participant_group'), 'participant_group_class_section', type_='foreignkey')
    op.create_foreign_key(
        'fk_pgcs_configuration_revision', 'participant_group_class_section', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_pgcs_participant_group', 'participant_group_class_section', 'participant_group',
        ['academic_year_id', 'configuration_revision_id', 'participant_group_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_pgcs_class_section', 'participant_group_class_section', 'class_section',
        ['academic_year_id', 'configuration_revision_id', 'class_section_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )

    op.drop_constraint(op.f('uq_teacher_availability_ay_ordinal'), 'teacher_availability', type_='unique')
    op.create_unique_constraint(
        'uq_teacher_availability_ay_ordinal', 'teacher_availability',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.drop_constraint(op.f('fk_teacher_availability_teacher'), 'teacher_availability', type_='foreignkey')
    op.drop_constraint(op.f('fk_teacher_availability_period'), 'teacher_availability', type_='foreignkey')
    op.drop_constraint(op.f('fk_teacher_availability_day'), 'teacher_availability', type_='foreignkey')
    op.create_foreign_key(
        'fk_teacher_availability_configuration_revision', 'teacher_availability', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_teacher_availability_period', 'teacher_availability', 'period',
        ['academic_year_id', 'configuration_revision_id', 'period_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_teacher_availability_teacher', 'teacher_availability', 'teacher',
        ['academic_year_id', 'configuration_revision_id', 'teacher_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_teacher_availability_day', 'teacher_availability', 'day',
        ['academic_year_id', 'configuration_revision_id', 'day_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )

    op.drop_constraint(op.f('fk_time_preference_teaching_requirement'), 'time_preference', type_='foreignkey')
    op.create_foreign_key(
        'fk_time_preference_teaching_requirement', 'time_preference', 'teaching_requirement',
        ['academic_year_id', 'configuration_revision_id', 'teaching_requirement_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_time_preference_configuration_revision', 'time_preference', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('fk_rbcs_class_section'), 'reserved_block_class_section', type_='foreignkey')
    op.drop_constraint(op.f('fk_rbcs_reserved_block'), 'reserved_block_class_section', type_='foreignkey')
    op.create_foreign_key(
        'fk_rbcs_class_section', 'reserved_block_class_section', 'class_section',
        ['academic_year_id', 'configuration_revision_id', 'class_section_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_rbcs_configuration_revision', 'reserved_block_class_section', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_rbcs_reserved_block', 'reserved_block_class_section', 'reserved_block',
        ['academic_year_id', 'configuration_revision_id', 'reserved_block_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('fk_rbs_day'), 'reserved_block_slot', type_='foreignkey')
    op.drop_constraint(op.f('fk_rbs_period'), 'reserved_block_slot', type_='foreignkey')
    op.drop_constraint(op.f('fk_rbs_reserved_block'), 'reserved_block_slot', type_='foreignkey')
    op.create_foreign_key(
        'fk_rbs_day', 'reserved_block_slot', 'day',
        ['academic_year_id', 'configuration_revision_id', 'day_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_rbs_period', 'reserved_block_slot', 'period',
        ['academic_year_id', 'configuration_revision_id', 'period_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_rbs_reserved_block', 'reserved_block_slot', 'reserved_block',
        ['academic_year_id', 'configuration_revision_id', 'reserved_block_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_rbs_configuration_revision', 'reserved_block_slot', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint(op.f('uq_fixed_placement_ay_natural_id'), 'fixed_placement', type_='unique')
    op.create_unique_constraint(
        'uq_fixed_placement_ay_natural_id', 'fixed_placement',
        ['academic_year_id', 'configuration_revision_id', 'natural_id'],
    )
    op.drop_constraint(op.f('uq_fixed_placement_ay_ordinal'), 'fixed_placement', type_='unique')
    op.create_unique_constraint(
        'uq_fixed_placement_ay_ordinal', 'fixed_placement',
        ['academic_year_id', 'configuration_revision_id', 'ordinal'],
    )
    op.drop_constraint(op.f('fk_fixed_placement_period'), 'fixed_placement', type_='foreignkey')
    op.drop_constraint(op.f('fk_fixed_placement_teaching_requirement'), 'fixed_placement', type_='foreignkey')
    op.drop_constraint(op.f('fk_fixed_placement_day'), 'fixed_placement', type_='foreignkey')
    op.create_foreign_key(
        'fk_fixed_placement_period', 'fixed_placement', 'period',
        ['academic_year_id', 'configuration_revision_id', 'period_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_fixed_placement_configuration_revision', 'fixed_placement', 'configuration_revision',
        ['academic_year_id', 'configuration_revision_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_fixed_placement_teaching_requirement', 'fixed_placement', 'teaching_requirement',
        ['academic_year_id', 'configuration_revision_id', 'teaching_requirement_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_fixed_placement_day', 'fixed_placement', 'day',
        ['academic_year_id', 'configuration_revision_id', 'day_id'],
        ['academic_year_id', 'configuration_revision_id', 'id'], ondelete='RESTRICT',
    )


def downgrade() -> None:
    op.drop_constraint('fk_time_preference_configuration_revision', 'time_preference', type_='foreignkey')
    op.drop_constraint('fk_time_preference_teaching_requirement', 'time_preference', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_time_preference_teaching_requirement'), 'time_preference', 'teaching_requirement',
        ['academic_year_id', 'teaching_requirement_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )

    op.drop_constraint('fk_teaching_requirement_resource', 'teaching_requirement', type_='foreignkey')
    op.drop_constraint('fk_teaching_requirement_participant_group', 'teaching_requirement', type_='foreignkey')
    op.drop_constraint('fk_teaching_requirement_activity', 'teaching_requirement', type_='foreignkey')
    op.drop_constraint('fk_teaching_requirement_configuration_revision', 'teaching_requirement', type_='foreignkey')
    op.drop_constraint('fk_teaching_requirement_teacher', 'teaching_requirement', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_teaching_requirement_activity'), 'teaching_requirement', 'activity',
        ['academic_year_id', 'activity_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_teaching_requirement_participant_group'), 'teaching_requirement', 'participant_group',
        ['academic_year_id', 'participant_group_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_teaching_requirement_resource'), 'teaching_requirement', 'resource',
        ['academic_year_id', 'resource_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_teaching_requirement_teacher'), 'teaching_requirement', 'teacher',
        ['academic_year_id', 'teacher_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_constraint('uq_teaching_requirement_ay_crid_id', 'teaching_requirement', type_='unique')
    op.drop_constraint('uq_teaching_requirement_ay_ordinal', 'teaching_requirement', type_='unique')
    op.create_unique_constraint(
        op.f('uq_teaching_requirement_ay_ordinal'), 'teaching_requirement', ['academic_year_id', 'ordinal'],
    )
    op.drop_constraint('uq_teaching_requirement_ay_natural_id', 'teaching_requirement', type_='unique')
    op.create_unique_constraint(
        op.f('uq_teaching_requirement_ay_natural_id'), 'teaching_requirement', ['academic_year_id', 'natural_id'],
    )
    op.drop_column('teaching_requirement', 'configuration_revision_id')

    op.drop_constraint('fk_teacher_availability_day', 'teacher_availability', type_='foreignkey')
    op.drop_constraint('fk_teacher_availability_teacher', 'teacher_availability', type_='foreignkey')
    op.drop_constraint('fk_teacher_availability_period', 'teacher_availability', type_='foreignkey')
    op.drop_constraint('fk_teacher_availability_configuration_revision', 'teacher_availability', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_teacher_availability_day'), 'teacher_availability', 'day',
        ['academic_year_id', 'day_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_teacher_availability_period'), 'teacher_availability', 'period',
        ['academic_year_id', 'period_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_teacher_availability_teacher'), 'teacher_availability', 'teacher',
        ['academic_year_id', 'teacher_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.drop_constraint('uq_teacher_availability_ay_ordinal', 'teacher_availability', type_='unique')
    op.create_unique_constraint(
        op.f('uq_teacher_availability_ay_ordinal'), 'teacher_availability', ['academic_year_id', 'ordinal'],
    )
    op.drop_column('teacher_availability', 'configuration_revision_id')

    op.drop_constraint('fk_teacher_configuration_revision', 'teacher', type_='foreignkey')
    op.drop_constraint('uq_teacher_ay_crid_id', 'teacher', type_='unique')
    op.drop_constraint('uq_teacher_ay_ordinal', 'teacher', type_='unique')
    op.create_unique_constraint(op.f('uq_teacher_ay_ordinal'), 'teacher', ['academic_year_id', 'ordinal'])
    op.drop_constraint('uq_teacher_ay_natural_id', 'teacher', type_='unique')
    op.create_unique_constraint(op.f('uq_teacher_ay_natural_id'), 'teacher', ['academic_year_id', 'natural_id'])
    op.drop_column('teacher', 'configuration_revision_id')

    op.drop_constraint('fk_schedule_version_configuration_revision', 'schedule_version', type_='foreignkey')
    op.drop_column('schedule_version', 'configuration_revision_id')

    op.drop_constraint('fk_resource_configuration_revision', 'resource', type_='foreignkey')
    op.drop_constraint('uq_resource_ay_crid_id', 'resource', type_='unique')
    op.drop_constraint('uq_resource_ay_ordinal', 'resource', type_='unique')
    op.create_unique_constraint(op.f('uq_resource_ay_ordinal'), 'resource', ['academic_year_id', 'ordinal'])
    op.drop_constraint('uq_resource_ay_natural_id', 'resource', type_='unique')
    op.create_unique_constraint(op.f('uq_resource_ay_natural_id'), 'resource', ['academic_year_id', 'natural_id'])
    op.drop_column('resource', 'configuration_revision_id')

    op.drop_constraint('fk_rbs_configuration_revision', 'reserved_block_slot', type_='foreignkey')
    op.drop_constraint('fk_rbs_reserved_block', 'reserved_block_slot', type_='foreignkey')
    op.drop_constraint('fk_rbs_period', 'reserved_block_slot', type_='foreignkey')
    op.drop_constraint('fk_rbs_day', 'reserved_block_slot', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_rbs_reserved_block'), 'reserved_block_slot', 'reserved_block',
        ['academic_year_id', 'reserved_block_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_rbs_period'), 'reserved_block_slot', 'period',
        ['academic_year_id', 'period_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_rbs_day'), 'reserved_block_slot', 'day',
        ['academic_year_id', 'day_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_column('reserved_block_slot', 'configuration_revision_id')

    op.drop_constraint('fk_rbcs_reserved_block', 'reserved_block_class_section', type_='foreignkey')
    op.drop_constraint('fk_rbcs_configuration_revision', 'reserved_block_class_section', type_='foreignkey')
    op.drop_constraint('fk_rbcs_class_section', 'reserved_block_class_section', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_rbcs_reserved_block'), 'reserved_block_class_section', 'reserved_block',
        ['academic_year_id', 'reserved_block_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_rbcs_class_section'), 'reserved_block_class_section', 'class_section',
        ['academic_year_id', 'class_section_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_column('reserved_block_class_section', 'configuration_revision_id')

    op.drop_constraint('fk_reserved_block_resource', 'reserved_block', type_='foreignkey')
    op.drop_constraint('fk_reserved_block_configuration_revision', 'reserved_block', type_='foreignkey')
    op.drop_constraint('fk_reserved_block_teacher', 'reserved_block', type_='foreignkey')
    op.drop_constraint('fk_reserved_block_activity', 'reserved_block', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_reserved_block_teacher'), 'reserved_block', 'teacher',
        ['academic_year_id', 'teacher_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_reserved_block_resource'), 'reserved_block', 'resource',
        ['academic_year_id', 'resource_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_reserved_block_activity'), 'reserved_block', 'activity',
        ['academic_year_id', 'activity_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_constraint('uq_reserved_block_ay_crid_id', 'reserved_block', type_='unique')
    op.drop_constraint('uq_reserved_block_ay_ordinal', 'reserved_block', type_='unique')
    op.create_unique_constraint(
        op.f('uq_reserved_block_ay_ordinal'), 'reserved_block', ['academic_year_id', 'ordinal'],
    )
    op.drop_constraint('uq_reserved_block_ay_natural_id', 'reserved_block', type_='unique')
    op.create_unique_constraint(
        op.f('uq_reserved_block_ay_natural_id'), 'reserved_block', ['academic_year_id', 'natural_id'],
    )
    op.drop_column('reserved_block', 'configuration_revision_id')

    op.drop_constraint('fk_period_configuration_revision', 'period', type_='foreignkey')
    op.drop_constraint('uq_period_ay_crid_id', 'period', type_='unique')
    op.drop_constraint('uq_period_ay_natural_id', 'period', type_='unique')
    op.create_unique_constraint(op.f('uq_period_ay_natural_id'), 'period', ['academic_year_id', 'natural_id'])
    op.drop_constraint('uq_period_ay_idx', 'period', type_='unique')
    op.create_unique_constraint(op.f('uq_period_ay_idx'), 'period', ['academic_year_id', 'idx'])
    op.drop_index('ix_period_ay_block', table_name='period')
    op.create_index(op.f('ix_period_ay_block'), 'period', ['academic_year_id', 'block_id'], unique=False)
    op.drop_column('period', 'configuration_revision_id')

    op.drop_constraint('fk_pgcs_class_section', 'participant_group_class_section', type_='foreignkey')
    op.drop_constraint('fk_pgcs_participant_group', 'participant_group_class_section', type_='foreignkey')
    op.drop_constraint('fk_pgcs_configuration_revision', 'participant_group_class_section', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_pgcs_participant_group'), 'participant_group_class_section', 'participant_group',
        ['academic_year_id', 'participant_group_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_pgcs_class_section'), 'participant_group_class_section', 'class_section',
        ['academic_year_id', 'class_section_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_column('participant_group_class_section', 'configuration_revision_id')

    op.drop_constraint('fk_participant_group_configuration_revision', 'participant_group', type_='foreignkey')
    op.drop_constraint('uq_participant_group_ay_crid_id', 'participant_group', type_='unique')
    op.drop_constraint('uq_participant_group_ay_ordinal', 'participant_group', type_='unique')
    op.create_unique_constraint(
        op.f('uq_participant_group_ay_ordinal'), 'participant_group', ['academic_year_id', 'ordinal'],
    )
    op.drop_constraint('uq_participant_group_ay_natural_id', 'participant_group', type_='unique')
    op.create_unique_constraint(
        op.f('uq_participant_group_ay_natural_id'), 'participant_group', ['academic_year_id', 'natural_id'],
    )
    op.drop_column('participant_group', 'configuration_revision_id')

    op.drop_constraint('fk_fixed_placement_day', 'fixed_placement', type_='foreignkey')
    op.drop_constraint('fk_fixed_placement_teaching_requirement', 'fixed_placement', type_='foreignkey')
    op.drop_constraint('fk_fixed_placement_configuration_revision', 'fixed_placement', type_='foreignkey')
    op.drop_constraint('fk_fixed_placement_period', 'fixed_placement', type_='foreignkey')
    op.create_foreign_key(
        op.f('fk_fixed_placement_day'), 'fixed_placement', 'day',
        ['academic_year_id', 'day_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.create_foreign_key(
        op.f('fk_fixed_placement_teaching_requirement'), 'fixed_placement', 'teaching_requirement',
        ['academic_year_id', 'teaching_requirement_id'], ['academic_year_id', 'id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        op.f('fk_fixed_placement_period'), 'fixed_placement', 'period',
        ['academic_year_id', 'period_id'], ['academic_year_id', 'id'], ondelete='RESTRICT',
    )
    op.drop_constraint('uq_fixed_placement_ay_ordinal', 'fixed_placement', type_='unique')
    op.create_unique_constraint(
        op.f('uq_fixed_placement_ay_ordinal'), 'fixed_placement', ['academic_year_id', 'ordinal'],
    )
    op.drop_constraint('uq_fixed_placement_ay_natural_id', 'fixed_placement', type_='unique')
    op.create_unique_constraint(
        op.f('uq_fixed_placement_ay_natural_id'), 'fixed_placement', ['academic_year_id', 'natural_id'],
    )
    op.drop_column('fixed_placement', 'configuration_revision_id')

    op.drop_constraint('fk_day_configuration_revision', 'day', type_='foreignkey')
    op.drop_constraint('uq_day_ay_crid_id', 'day', type_='unique')
    op.drop_constraint('uq_day_ay_natural_id', 'day', type_='unique')
    op.create_unique_constraint(op.f('uq_day_ay_natural_id'), 'day', ['academic_year_id', 'natural_id'])
    op.drop_constraint('uq_day_ay_idx', 'day', type_='unique')
    op.create_unique_constraint(op.f('uq_day_ay_idx'), 'day', ['academic_year_id', 'idx'])
    op.drop_column('day', 'configuration_revision_id')

    op.drop_constraint('fk_class_section_configuration_revision', 'class_section', type_='foreignkey')
    op.drop_constraint('uq_class_section_ay_crid_id', 'class_section', type_='unique')
    op.drop_constraint('uq_class_section_ay_ordinal', 'class_section', type_='unique')
    op.create_unique_constraint(
        op.f('uq_class_section_ay_ordinal'), 'class_section', ['academic_year_id', 'ordinal'],
    )
    op.drop_constraint('uq_class_section_ay_natural_id', 'class_section', type_='unique')
    op.create_unique_constraint(
        op.f('uq_class_section_ay_natural_id'), 'class_section', ['academic_year_id', 'natural_id'],
    )
    op.drop_column('class_section', 'configuration_revision_id')

    op.drop_constraint('fk_activity_configuration_revision', 'activity', type_='foreignkey')
    op.drop_constraint('uq_activity_ay_crid_id', 'activity', type_='unique')
    op.drop_constraint('uq_activity_ay_ordinal', 'activity', type_='unique')
    op.create_unique_constraint(op.f('uq_activity_ay_ordinal'), 'activity', ['academic_year_id', 'ordinal'])
    op.drop_constraint('uq_activity_ay_natural_id', 'activity', type_='unique')
    op.create_unique_constraint(op.f('uq_activity_ay_natural_id'), 'activity', ['academic_year_id', 'natural_id'])
    op.drop_column('activity', 'configuration_revision_id')

    op.drop_constraint('fk_academic_year_draft_revision', 'academic_year', type_='foreignkey')
    op.drop_constraint('fk_academic_year_published_revision', 'academic_year', type_='foreignkey')
    op.drop_column('academic_year', 'draft_revision_id')
    op.drop_column('academic_year', 'published_revision_id')

    op.drop_index(
        'uq_configuration_revision_one_published', table_name='configuration_revision',
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.drop_index(
        'uq_configuration_revision_one_draft', table_name='configuration_revision',
        postgresql_where=sa.text("status = 'DRAFT'"),
    )
    op.drop_table('configuration_revision')
