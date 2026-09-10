"""SQLAlchemy ORM models for the Phase 3A2/3A3 persisted scheduling schema.

Persists the complete current `SchedulingProblem` input surface (School,
AcademicYear, Day, Period, ClassSection, ParticipantGroup, Teacher,
TeacherAvailability, Activity, Resource, TeachingRequirement,
TimePreference, ReservedBlock, FixedPlacement, and their child/join
tables) as a snapshot scoped to one `academic_year_id` -- see
`docs/DECISIONS.md` #26 and `docs/ARCHITECTURE.md` for the locked
schema/identity/isolation/ordering/delete-semantics rules these models
implement exactly.

Phase 3A3.1 (`docs/DECISIONS.md` #31) adds `Schedule`, `ScheduleVersion`,
`ScheduleEntry`, and `LockedOccurrence`: one immutable generated-schedule
history per `academic_year_id`. Schema only -- no domain <-> persistence
mapping, repository, application service, or API route exists for these
tables yet (that is Phase 3A3.2+).

Deliberately separate classes from `domain/`'s frozen dataclasses (see
`persistence/base.py`). Explicit ORM -> domain mapping for the
configuration tables above already exists (`persistence/mappers.py`,
Phase 3A2.2); mapping for `Schedule`/`ScheduleVersion`/`ScheduleEntry`/
`LockedOccurrence` is not part of Phase 3A3.1 and begins in Phase
3A3.2. These classes carry no `relationship()` navigation on purpose --
nothing in this phase reads the ORM object graph, only plain
columns/constraints, so adding `relationship()` now would be unused
surface area.

Identity rule: every table's surrogate `id` (where one exists) is a
`BIGINT GENERATED ALWAYS AS IDENTITY`, persistence-only, never exposed
outside `persistence/`. Every domain string ID is stored verbatim in a
`natural_id` column. Cross-entity references within one academic year
are enforced by PostgreSQL itself via composite foreign keys of the form
`FOREIGN KEY (academic_year_id, x_id) REFERENCES x (academic_year_id,
id)` -- every table that is a valid FK target additionally carries
`UNIQUE(academic_year_id, id)` to serve as that composite target.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from school_timetable.persistence.base import Base


class School(Base):
    __tablename__ = "school"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("natural_id", name="uq_school_natural_id"),
    )


class AcademicYear(Base):
    __tablename__ = "academic_year"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    school_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("school_id", "natural_id", name="uq_academic_year_school_natural_id"),
        ForeignKeyConstraint(
            ["school_id"], ["school.id"], ondelete="CASCADE", name="fk_academic_year_school"
        ),
    )


class Day(Base):
    """One school day. `idx` is the domain's own ordering field
    (`Day.index`) and doubles as the tuple-order-preserving column for
    `SchedulingProblem.days` -- no separate `ordinal` is needed here."""

    __tablename__ = "day"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_day_ay_natural_id"),
        UniqueConstraint("academic_year_id", "idx", name="uq_day_ay_idx"),
        UniqueConstraint("academic_year_id", "id", name="uq_day_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_day_academic_year"
        ),
    )


class Period(Base):
    """One instructional period slot shared across every day of the week.
    `idx` is the domain's own ordering field (`Period.index`); `block_id`
    is a bare grouping label (no separate `Block` entity exists)."""

    __tablename__ = "period"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    idx: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    block_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_instructional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_period_ay_natural_id"),
        UniqueConstraint("academic_year_id", "idx", name="uq_period_ay_idx"),
        UniqueConstraint("academic_year_id", "id", name="uq_period_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_period_academic_year"
        ),
        Index("ix_period_ay_block", "academic_year_id", "block_id"),
    )


class ClassSection(Base):
    __tablename__ = "class_section"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_class_section_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_class_section_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_class_section_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_class_section_academic_year"
        ),
    )


class ParticipantGroup(Base):
    __tablename__ = "participant_group"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    """WHOLE_CLASS/SUBGROUP/MERGED_CLASSES (`DECISIONS.md` #33). No
    `server_default` -- deliberately fail-closed: a row without an
    explicit role must never be silently classified."""
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_participant_group_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_participant_group_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_participant_group_ay_id"),
        CheckConstraint(
            "role IN ('WHOLE_CLASS', 'SUBGROUP', 'MERGED_CLASSES')", name="ck_participant_group_role",
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_participant_group_academic_year",
        ),
    )


class ParticipantGroupClassSection(Base):
    """Child rows for `ParticipantGroup.class_sections` (a tuple).
    `ordinal` preserves exact tuple order; no surrogate `id` is needed --
    this row has no identity beyond "the Nth class of this group" and is
    never referenced from elsewhere."""

    __tablename__ = "participant_group_class_section"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    participant_group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    class_section_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("participant_group_id", "ordinal", name="pk_participant_group_class_section"),
        UniqueConstraint(
            "participant_group_id", "class_section_id", name="uq_pgcs_group_class"
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_pgcs_academic_year"
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "participant_group_id"],
            ["participant_group.academic_year_id", "participant_group.id"],
            ondelete="CASCADE",
            name="fk_pgcs_participant_group",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "class_section_id"],
            ["class_section.academic_year_id", "class_section.id"],
            ondelete="RESTRICT",
            name="fk_pgcs_class_section",
        ),
    )


class Teacher(Base):
    __tablename__ = "teacher"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    """Owner Decision #37: replaces the former single `name` column.
    No uniqueness on either -- two teachers may share an identical
    first+last name; `natural_id` remains the real identity."""
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_teacher_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_teacher_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_teacher_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_teacher_academic_year"
        ),
    )


class TeacherAvailability(Base):
    """An availability override for one teacher at one (day, period).
    Identity mirrors the domain's own natural composite key exactly --
    no surrogate `id`."""

    __tablename__ = "teacher_availability"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teacher_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("teacher_id", "day_id", "period_id", name="pk_teacher_availability"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_teacher_availability_ay_ordinal"),
        CheckConstraint(
            "status IN ('AVAILABLE', 'PREFER_NOT', 'UNAVAILABLE')",
            name="ck_teacher_availability_status",
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_teacher_availability_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teacher_id"],
            ["teacher.academic_year_id", "teacher.id"],
            ondelete="CASCADE",
            name="fk_teacher_availability_teacher",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "day_id"],
            ["day.academic_year_id", "day.id"],
            ondelete="RESTRICT",
            name="fk_teacher_availability_day",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "period_id"],
            ["period.academic_year_id", "period.id"],
            ondelete="RESTRICT",
            name="fk_teacher_availability_period",
        ),
        Index("ix_teacher_availability_teacher", "teacher_id"),
    )


class Activity(Base):
    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="ORDINARY")
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_activity_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_activity_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_activity_ay_id"),
        CheckConstraint("kind IN ('ORDINARY', 'CLUB')", name="ck_activity_kind"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_activity_academic_year"
        ),
    )


class Resource(Base):
    __tablename__ = "resource"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_resource_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_resource_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_resource_ay_id"),
        CheckConstraint("capacity > 0", name="ck_resource_capacity_positive"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_resource_academic_year"
        ),
    )


class TeachingRequirement(Base):
    """The core scheduling input. Embeds `LessonBlockPolicy`
    (`block_mode`, `block_sizes`), `DistributionPolicy`
    (`min_distinct_days`, `max_periods_per_day`), and `ResourceRequirement`
    (`resource_id`) as typed columns -- see docs/DECISIONS.md #26 for why
    none of this is JSONB. `split_group_id` is a bare synchronization
    label, never a FK (no `SplitGroup` entity exists)."""

    __tablename__ = "teaching_requirement"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    teacher_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    participant_group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    weekly_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    block_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="FLEXIBLE")
    block_sizes: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default="{}"
    )
    min_distinct_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    max_periods_per_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    split_group_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_teaching_requirement_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_teaching_requirement_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_teaching_requirement_ay_id"),
        CheckConstraint("weekly_periods > 0", name="ck_teaching_requirement_weekly_periods_positive"),
        CheckConstraint(
            "block_mode IN ('REQUIRED', 'PREFERRED', 'FLEXIBLE')", name="ck_teaching_requirement_block_mode"
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_teaching_requirement_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teacher_id"],
            ["teacher.academic_year_id", "teacher.id"],
            ondelete="RESTRICT",
            name="fk_teaching_requirement_teacher",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "activity_id"],
            ["activity.academic_year_id", "activity.id"],
            ondelete="RESTRICT",
            name="fk_teaching_requirement_activity",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "participant_group_id"],
            ["participant_group.academic_year_id", "participant_group.id"],
            ondelete="RESTRICT",
            name="fk_teaching_requirement_participant_group",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "resource_id"],
            ["resource.academic_year_id", "resource.id"],
            ondelete="RESTRICT",
            name="fk_teaching_requirement_resource",
        ),
        Index("ix_teaching_requirement_ay_teacher", "academic_year_id", "teacher_id"),
        Index("ix_teaching_requirement_ay_participant_group", "academic_year_id", "participant_group_id"),
        Index(
            "ix_teaching_requirement_ay_split_group",
            "academic_year_id",
            "split_group_id",
            postgresql_where=text("split_group_id IS NOT NULL"),
        ),
    )


class TimePreference(Base):
    """Child rows for `TeachingRequirement.time_preferences` (a tuple,
    0..N per requirement). `preferred_period_indexes` stores
    `TimePreference.preferred_periods` -- `Period.index` integers, not
    `Period.id` references -- exactly as the domain models it."""

    __tablename__ = "time_preference"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teaching_requirement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    preferred_period_indexes: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False)
    weight: Mapped[str] = mapped_column(Text, nullable=False, server_default="MEDIUM")

    __table_args__ = (
        PrimaryKeyConstraint("teaching_requirement_id", "ordinal", name="pk_time_preference"),
        CheckConstraint("weight IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_time_preference_weight"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_time_preference_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teaching_requirement_id"],
            ["teaching_requirement.academic_year_id", "teaching_requirement.id"],
            ondelete="CASCADE",
            name="fk_time_preference_teaching_requirement",
        ),
    )


class ReservedBlock(Base):
    __tablename__ = "reserved_block"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    activity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teacher_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_reserved_block_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_reserved_block_ay_ordinal"),
        UniqueConstraint("academic_year_id", "id", name="uq_reserved_block_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_reserved_block_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "activity_id"],
            ["activity.academic_year_id", "activity.id"],
            ondelete="RESTRICT",
            name="fk_reserved_block_activity",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teacher_id"],
            ["teacher.academic_year_id", "teacher.id"],
            ondelete="RESTRICT",
            name="fk_reserved_block_teacher",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "resource_id"],
            ["resource.academic_year_id", "resource.id"],
            ondelete="RESTRICT",
            name="fk_reserved_block_resource",
        ),
    )


class ReservedBlockClassSection(Base):
    """Child rows for `ReservedBlock.class_sections` (a tuple)."""

    __tablename__ = "reserved_block_class_section"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_block_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    class_section_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("reserved_block_id", "ordinal", name="pk_reserved_block_class_section"),
        UniqueConstraint("reserved_block_id", "class_section_id", name="uq_rbcs_block_class"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_rbcs_academic_year"
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "reserved_block_id"],
            ["reserved_block.academic_year_id", "reserved_block.id"],
            ondelete="CASCADE",
            name="fk_rbcs_reserved_block",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "class_section_id"],
            ["class_section.academic_year_id", "class_section.id"],
            ondelete="RESTRICT",
            name="fk_rbcs_class_section",
        ),
    )


class ReservedBlockSlot(Base):
    """Child rows for `ReservedBlock.slots` (a tuple of `TimeSlot`)."""

    __tablename__ = "reserved_block_slot"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_block_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("reserved_block_id", "ordinal", name="pk_reserved_block_slot"),
        UniqueConstraint("reserved_block_id", "day_id", "period_id", name="uq_rbs_block_day_period"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_rbs_academic_year"
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "reserved_block_id"],
            ["reserved_block.academic_year_id", "reserved_block.id"],
            ondelete="CASCADE",
            name="fk_rbs_reserved_block",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "day_id"],
            ["day.academic_year_id", "day.id"],
            ondelete="RESTRICT",
            name="fk_rbs_day",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "period_id"],
            ["period.academic_year_id", "period.id"],
            ondelete="RESTRICT",
            name="fk_rbs_period",
        ),
    )


class FixedPlacement(Base):
    """Pins one lesson of a `TeachingRequirement` to an exact slot."""

    __tablename__ = "fixed_placement"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    natural_id: Mapped[str] = mapped_column(Text, nullable=False)
    teaching_requirement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "natural_id", name="uq_fixed_placement_ay_natural_id"),
        UniqueConstraint("academic_year_id", "ordinal", name="uq_fixed_placement_ay_ordinal"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_fixed_placement_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teaching_requirement_id"],
            ["teaching_requirement.academic_year_id", "teaching_requirement.id"],
            ondelete="CASCADE",
            name="fk_fixed_placement_teaching_requirement",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "day_id"],
            ["day.academic_year_id", "day.id"],
            ondelete="RESTRICT",
            name="fk_fixed_placement_day",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "period_id"],
            ["period.academic_year_id", "period.id"],
            ondelete="RESTRICT",
            name="fk_fixed_placement_period",
        ),
        Index("ix_fixed_placement_teaching_requirement", "teaching_requirement_id"),
    )


class Schedule(Base):
    """The one canonical generated-schedule aggregate for one academic
    year (Phase 3A3.1, Decision #31 Owner Decision 1). No natural ID --
    publicly identified by `(school_id, academic_year_id)` alone, the
    same two natural IDs `/config` already uses. `active_version_id` is
    nullable only for the brief window between creating this row and
    creating its first `ScheduleVersion`."""

    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("academic_year_id", name="uq_schedule_academic_year_id"),
        UniqueConstraint("academic_year_id", "id", name="uq_schedule_ay_id"),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE", name="fk_schedule_academic_year"
        ),
        # Circular reference: `schedule` <-> `schedule_version` mutually
        # reference each other. `use_alter=True` defers this specific
        # constraint to a post-CREATE-TABLE `ALTER TABLE ADD CONSTRAINT`
        # (and, on drop, an `ALTER TABLE DROP CONSTRAINT` before either
        # table is dropped) -- SQLAlchemy's standard mechanism for a
        # genuine two-table FK cycle. No `DEFERRABLE` is used or needed:
        # the creation sequence (insert `schedule` with `active_version_id`
        # NULL, insert version 1, then `UPDATE schedule SET
        # active_version_id = ...`) never needs the two rows to exist
        # simultaneously within one statement -- see Decision #31.
        ForeignKeyConstraint(
            ["id", "active_version_id"],
            ["schedule_version.schedule_id", "schedule_version.id"],
            name="fk_schedule_active_version",
            use_alter=True,
            # No `ondelete` -- PostgreSQL's default `NO ACTION`, per
            # Decision #31's "Delete action for the lineage/active-version
            # references" (deliberately not `RESTRICT`, so a whole-
            # snapshot `academic_year` root delete can still cascade the
            # entire version graph away in one statement).
        ),
    )


class ScheduleVersion(Base):
    """One immutable generated/edited schedule snapshot (Phase 3A3.1,
    Decision #31). No natural ID -- publicly identified by
    `(school_id, academic_year_id, version_number)`. `solver_status`
    only ever persists `OPTIMAL`/`FEASIBLE`; `INFEASIBLE`/
    `INVALID_INPUT`/`ERROR` are never persisted at all (Owner Decision 4's
    transaction-boundary flow only writes after the independent verifier
    has passed)."""

    __tablename__ = "schedule_version"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schedule_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    solver_status: Mapped[str] = mapped_column(Text, nullable=False)
    total_soft_penalty: Mapped[int] = mapped_column(Integer, nullable=False)
    wall_time_seconds: Mapped[float] = mapped_column(Double, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("schedule_id", "version_number", name="uq_schedule_version_schedule_version_number"),
        UniqueConstraint("schedule_id", "id", name="uq_schedule_version_schedule_id"),
        UniqueConstraint("academic_year_id", "id", name="uq_schedule_version_ay_id"),
        CheckConstraint(
            "solver_status IN ('OPTIMAL', 'FEASIBLE')", name="ck_schedule_version_solver_status"
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_schedule_version_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "schedule_id"],
            ["schedule.academic_year_id", "schedule.id"],
            ondelete="CASCADE",
            name="fk_schedule_version_schedule",
        ),
        # Self-referential lineage FK -- not the schedule<->schedule_version
        # cycle above, so no `use_alter` is needed (a table may always
        # reference its own primary/unique key within its own CREATE
        # TABLE). No `ondelete` -- PostgreSQL's default `NO ACTION`, same
        # reasoning as `schedule.active_version_id` above.
        ForeignKeyConstraint(
            ["schedule_id", "parent_version_id"],
            ["schedule_version.schedule_id", "schedule_version.id"],
            name="fk_schedule_version_parent",
        ),
    )


class ScheduleEntry(Base):
    """One placed (day, period) decision within an immutable
    `ScheduleVersion` (Phase 3A3.1, Decision #31). No natural ID -- this
    row has no domain-facing identity of its own, matching
    `teacher_availability`/`time_preference`'s existing no-natural-ID
    pattern. `ordinal` exists because `Schedule.entries` is a
    `tuple[ScheduleEntry, ...]`, not a set -- SQL row order is never
    guaranteed, so exact round-trip reconstruction needs an explicit
    ordinal, the same tuple-needs-an-ordinal rule as every Phase 3A2.1
    table (Decision #26). Deliberately does NOT store `activity_id`/
    `teacher_id`/`participant_group_id`/`resource_id`/`class_sections`
    -- all fully re-derivable by joining back to the referenced
    `teaching_requirement`/`reserved_block` at read time."""

    __tablename__ = "schedule_entry"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schedule_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    day_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teaching_requirement_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reserved_block_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("schedule_version_id", "ordinal", name="uq_schedule_entry_version_ordinal"),
        CheckConstraint(
            "source IN ('REQUIREMENT', 'RESERVED_BLOCK')", name="ck_schedule_entry_source"
        ),
        CheckConstraint(
            "(source = 'REQUIREMENT') = (teaching_requirement_id IS NOT NULL) "
            "AND (source = 'RESERVED_BLOCK') = (reserved_block_id IS NOT NULL)",
            name="ck_schedule_entry_source_reference",
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_schedule_entry_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "schedule_version_id"],
            ["schedule_version.academic_year_id", "schedule_version.id"],
            ondelete="CASCADE",
            name="fk_schedule_entry_schedule_version",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "day_id"],
            ["day.academic_year_id", "day.id"],
            ondelete="RESTRICT",
            name="fk_schedule_entry_day",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "period_id"],
            ["period.academic_year_id", "period.id"],
            ondelete="RESTRICT",
            name="fk_schedule_entry_period",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teaching_requirement_id"],
            ["teaching_requirement.academic_year_id", "teaching_requirement.id"],
            ondelete="RESTRICT",
            name="fk_schedule_entry_teaching_requirement",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "reserved_block_id"],
            ["reserved_block.academic_year_id", "reserved_block.id"],
            ondelete="RESTRICT",
            name="fk_schedule_entry_reserved_block",
        ),
    )


class LockedOccurrence(Base):
    """Persisted `OccurrenceKey` set for one `ScheduleVersion` (Phase
    3A3.1, Decision #31). No surrogate PK -- the natural composite key
    mirrors the domain's own `OccurrenceKey(requirement_id, day_id,
    anchor_period_id)` exactly, scoped to its version, matching
    `teacher_availability`'s existing no-surrogate-PK design. No
    `ordinal`: unlike `schedule_entry` (a tuple), the in-memory type
    here is `Schedule.locked_occurrences: frozenset[OccurrenceKey]` --
    a genuine set with no order to preserve. A freshly generated version
    1 normally has zero rows here."""

    __tablename__ = "locked_occurrence"

    academic_year_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schedule_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    teaching_requirement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    anchor_period_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "schedule_version_id", "teaching_requirement_id", "day_id", "anchor_period_id",
            name="pk_locked_occurrence",
        ),
        ForeignKeyConstraint(
            ["academic_year_id"], ["academic_year.id"], ondelete="CASCADE",
            name="fk_locked_occurrence_academic_year",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "schedule_version_id"],
            ["schedule_version.academic_year_id", "schedule_version.id"],
            ondelete="CASCADE",
            name="fk_locked_occurrence_schedule_version",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "teaching_requirement_id"],
            ["teaching_requirement.academic_year_id", "teaching_requirement.id"],
            ondelete="RESTRICT",
            name="fk_locked_occurrence_teaching_requirement",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "day_id"],
            ["day.academic_year_id", "day.id"],
            ondelete="RESTRICT",
            name="fk_locked_occurrence_day",
        ),
        ForeignKeyConstraint(
            ["academic_year_id", "anchor_period_id"],
            ["period.academic_year_id", "period.id"],
            ondelete="RESTRICT",
            name="fk_locked_occurrence_anchor_period",
        ),
    )
