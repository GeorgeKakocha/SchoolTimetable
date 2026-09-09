"""Persistence -> domain mapping (Phase 3A2.2).

Pure, deterministic mapping from already-loaded SQLAlchemy ORM rows
(`persistence/models.py`) to frozen `domain/` objects. This module never
talks to a database -- no `Session`, no query, no engine, no connection
-- and never imports `fixtures/`, `application/`, `api/`, or
`scheduling/`. Every function here takes plain Python objects (ORM rows
already fetched by someone else, or plain collections of them) and
returns a frozen domain object; nothing is looked up lazily via an ORM
relationship.

The reverse direction (domain -> persistence) is intentionally NOT
implemented here for the Phase 3A2.1 configuration tables. Domain
objects reference each other only by natural string ID, while ORM rows
reference each other by surrogate `BIGINT` FK -- writing configuration
requires whole-graph identity resolution across every entity in one
academic year, which is a different, aggregate-aware concern. That is a
TEST-ONLY aggregate writer, Phase 3A2.3 -- see `docs/DECISIONS.md` #27.
No `save`/`create`/`upsert`/`from_domain`/CRUD for configuration exists
in this module. (Phase 3A3.2 adds `schedule_entry_to_domain`/
`locked_occurrence_to_domain` -- still persistence -> domain only; the
production domain -> persistence write path for schedule data lives in
`persistence/schedule_repository.py`, not here.)
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    PreferenceWeight,
    TeachingRequirement,
    TimePreference,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import OccurrenceKey
from school_timetable.domain.school import School
from school_timetable.persistence import models as orm


def _resolve(mapping: dict[int, str], entity_name: str, surrogate_id: int) -> str:
    """Resolve one surrogate FK id to its natural_id, failing clearly
    (rather than silently returning the surrogate or inventing an ID) if
    the caller's `NaturalIdLookup` was not built with that row."""
    try:
        return mapping[surrogate_id]
    except KeyError:
        raise KeyError(
            f"no natural_id registered for {entity_name} surrogate id {surrogate_id!r} "
            "-- the supplied NaturalIdLookup was not built from that row"
        ) from None


@dataclass(frozen=True)
class NaturalIdLookup:
    """Persistence-only surrogate-id -> natural_id context, one dict per
    entity type that another table references by surrogate FK. Built
    once (via `.build()`) from whatever rows the caller already fetched
    for one academic year; never queries a database itself. Every
    `*_to_domain` mapper below that needs to resolve a sibling FK takes
    one of these instead of a `Session`."""

    days: dict[int, str] = field(default_factory=dict)
    periods: dict[int, str] = field(default_factory=dict)
    class_sections: dict[int, str] = field(default_factory=dict)
    teachers: dict[int, str] = field(default_factory=dict)
    activities: dict[int, str] = field(default_factory=dict)
    participant_groups: dict[int, str] = field(default_factory=dict)
    resources: dict[int, str] = field(default_factory=dict)
    teaching_requirements: dict[int, str] = field(default_factory=dict)
    reserved_blocks: dict[int, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        days: Iterable[orm.Day] = (),
        periods: Iterable[orm.Period] = (),
        class_sections: Iterable[orm.ClassSection] = (),
        teachers: Iterable[orm.Teacher] = (),
        activities: Iterable[orm.Activity] = (),
        participant_groups: Iterable[orm.ParticipantGroup] = (),
        resources: Iterable[orm.Resource] = (),
        teaching_requirements: Iterable[orm.TeachingRequirement] = (),
        reserved_blocks: Iterable[orm.ReservedBlock] = (),
    ) -> NaturalIdLookup:
        return cls(
            days={row.id: row.natural_id for row in days},
            periods={row.id: row.natural_id for row in periods},
            class_sections={row.id: row.natural_id for row in class_sections},
            teachers={row.id: row.natural_id for row in teachers},
            activities={row.id: row.natural_id for row in activities},
            participant_groups={row.id: row.natural_id for row in participant_groups},
            resources={row.id: row.natural_id for row in resources},
            teaching_requirements={row.id: row.natural_id for row in teaching_requirements},
            reserved_blocks={row.id: row.natural_id for row in reserved_blocks},
        )

    def day(self, surrogate_id: int) -> str:
        return _resolve(self.days, "Day", surrogate_id)

    def period(self, surrogate_id: int) -> str:
        return _resolve(self.periods, "Period", surrogate_id)

    def class_section(self, surrogate_id: int) -> str:
        return _resolve(self.class_sections, "ClassSection", surrogate_id)

    def teacher(self, surrogate_id: int) -> str:
        return _resolve(self.teachers, "Teacher", surrogate_id)

    def activity(self, surrogate_id: int) -> str:
        return _resolve(self.activities, "Activity", surrogate_id)

    def participant_group(self, surrogate_id: int) -> str:
        return _resolve(self.participant_groups, "ParticipantGroup", surrogate_id)

    def resource(self, surrogate_id: int) -> str:
        return _resolve(self.resources, "Resource", surrogate_id)

    def teaching_requirement(self, surrogate_id: int) -> str:
        return _resolve(self.teaching_requirements, "TeachingRequirement", surrogate_id)

    def reserved_block(self, surrogate_id: int) -> str:
        return _resolve(self.reserved_blocks, "ReservedBlock", surrogate_id)


# -- Simple entities: no sibling FK to resolve, no ordinal to sort by. ------


def school_to_domain(row: orm.School) -> School:
    return School(id=row.natural_id, name=row.name)


def academic_year_to_domain(row: orm.AcademicYear) -> AcademicYear:
    return AcademicYear(id=row.natural_id, label=row.label)


def day_to_domain(row: orm.Day) -> Day:
    return Day(id=row.natural_id, name=row.name, index=row.idx)


def period_to_domain(row: orm.Period) -> Period:
    return Period(
        id=row.natural_id,
        name=row.name,
        index=row.idx,
        block_id=row.block_id,
        is_instructional=row.is_instructional,
    )


def class_section_to_domain(row: orm.ClassSection) -> ClassSection:
    return ClassSection(id=row.natural_id, name=row.name)


def teacher_to_domain(row: orm.Teacher) -> Teacher:
    return Teacher(id=row.natural_id, first_name=row.first_name, last_name=row.last_name)


def activity_to_domain(row: orm.Activity) -> Activity:
    return Activity(id=row.natural_id, name=row.name, kind=ActivityKind(row.kind))


def resource_to_domain(row: orm.Resource) -> Resource:
    return Resource(id=row.natural_id, name=row.name, capacity=row.capacity)


# -- Entities needing sibling-FK resolution and/or ordinal-ordered children. -


def participant_group_to_domain(
    row: orm.ParticipantGroup,
    memberships: Sequence[orm.ParticipantGroupClassSection],
    lookup: NaturalIdLookup,
) -> ParticipantGroup:
    """`memberships` may be supplied in any order -- reconstructs
    `class_sections` sorted by `ordinal`, never by database return
    order, so tuple equality with the original domain object holds."""
    ordered = sorted(memberships, key=lambda m: m.ordinal)
    return ParticipantGroup(
        id=row.natural_id,
        name=row.name,
        class_sections=tuple(lookup.class_section(m.class_section_id) for m in ordered),
        role=ParticipantGroupRole(row.role),
    )


def teacher_availability_to_domain(row: orm.TeacherAvailability, lookup: NaturalIdLookup) -> TeacherAvailability:
    return TeacherAvailability(
        teacher_id=lookup.teacher(row.teacher_id),
        day_id=lookup.day(row.day_id),
        period_id=lookup.period(row.period_id),
        status=AvailabilityStatus(row.status),
    )


def time_preference_to_domain(row: orm.TimePreference) -> TimePreference:
    """`preferred_period_indexes` are `Period.index` integers, not
    `Period.id` references -- mapped back verbatim, never resolved
    through `NaturalIdLookup`, matching the domain's own index-based
    semantics exactly."""
    return TimePreference(
        preferred_periods=tuple(row.preferred_period_indexes),
        weight=PreferenceWeight(row.weight),
    )


def teaching_requirement_to_domain(
    row: orm.TeachingRequirement,
    time_preference_rows: Sequence[orm.TimePreference],
    lookup: NaturalIdLookup,
) -> TeachingRequirement:
    """`time_preference_rows` may be supplied in any order -- sorted by
    `ordinal` before mapping, so `time_preferences` tuple order is
    exact."""
    ordered_prefs = sorted(time_preference_rows, key=lambda tp: tp.ordinal)
    resource_requirement = (
        None
        if row.resource_id is None
        else ResourceRequirement(resource_id=lookup.resource(row.resource_id))
    )
    return TeachingRequirement(
        id=row.natural_id,
        teacher_id=lookup.teacher(row.teacher_id),
        activity_id=lookup.activity(row.activity_id),
        participant_group_id=lookup.participant_group(row.participant_group_id),
        weekly_periods=row.weekly_periods,
        block_policy=LessonBlockPolicy(
            mode=BlockPolicyMode(row.block_mode),
            block_sizes=tuple(row.block_sizes),
        ),
        distribution_policy=DistributionPolicy(
            min_distinct_days=row.min_distinct_days,
            max_periods_per_day=row.max_periods_per_day,
        ),
        time_preferences=tuple(time_preference_to_domain(tp) for tp in ordered_prefs),
        resource_requirement=resource_requirement,
        # A bare synchronization label, not a reference -- stored and
        # returned verbatim, never normalized or resolved.
        split_group_id=row.split_group_id,
    )


def reserved_block_to_domain(
    row: orm.ReservedBlock,
    class_section_rows: Sequence[orm.ReservedBlockClassSection],
    slot_rows: Sequence[orm.ReservedBlockSlot],
    lookup: NaturalIdLookup,
) -> ReservedBlock:
    """`class_section_rows`/`slot_rows` may each be supplied in any
    order -- both sorted by their own `ordinal` before mapping."""
    ordered_classes = sorted(class_section_rows, key=lambda r: r.ordinal)
    ordered_slots = sorted(slot_rows, key=lambda r: r.ordinal)
    return ReservedBlock(
        id=row.natural_id,
        name=row.name,
        activity_id=lookup.activity(row.activity_id),
        class_sections=tuple(lookup.class_section(r.class_section_id) for r in ordered_classes),
        slots=tuple(
            TimeSlot(day_id=lookup.day(r.day_id), period_id=lookup.period(r.period_id))
            for r in ordered_slots
        ),
        teacher_id=None if row.teacher_id is None else lookup.teacher(row.teacher_id),
    )


def fixed_placement_to_domain(row: orm.FixedPlacement, lookup: NaturalIdLookup) -> FixedPlacement:
    return FixedPlacement(
        id=row.natural_id,
        requirement_id=lookup.teaching_requirement(row.teaching_requirement_id),
        slot=TimeSlot(day_id=lookup.day(row.day_id), period_id=lookup.period(row.period_id)),
    )


# -- Schedule persistence mapping (Phase 3A3.2, Decision #31). ---------------
#
# `schedule_entry` deliberately does not store `activity_id`/`teacher_id`/
# `participant_group_id`/`resource_id`/`class_sections` -- every one of
# these is re-derived here by joining back to the referenced
# `TeachingRequirement`/`ReservedBlock`/`ParticipantGroup` *domain*
# objects the caller already loaded (via `problem_repository.py`'s
# existing mappers), exactly mirroring `scheduling/result_builder.py`'s
# own construction of a freshly solved `ScheduleEntry` -- this is the
# same derivation, just read back from persistence instead of from a
# solved CP-SAT model.


def schedule_entry_to_domain(
    row: orm.ScheduleEntry,
    lookup: NaturalIdLookup,
    requirements_by_natural_id: dict[str, TeachingRequirement],
    reserved_blocks_by_natural_id: dict[str, ReservedBlock],
    participant_groups_by_natural_id: dict[str, ParticipantGroup],
) -> ScheduleEntry:
    day_id = lookup.day(row.day_id)
    period_id = lookup.period(row.period_id)

    if row.source == EntrySource.REQUIREMENT.value:
        requirement_id = lookup.teaching_requirement(row.teaching_requirement_id)
        requirement = requirements_by_natural_id[requirement_id]
        group = participant_groups_by_natural_id[requirement.participant_group_id]
        return ScheduleEntry(
            source=EntrySource.REQUIREMENT,
            activity_id=requirement.activity_id,
            day_id=day_id,
            period_id=period_id,
            class_sections=group.class_sections,
            teacher_id=requirement.teacher_id,
            participant_group_id=requirement.participant_group_id,
            resource_id=(
                requirement.resource_requirement.resource_id
                if requirement.resource_requirement
                else None
            ),
            requirement_id=requirement.id,
        )

    reserved_block_id = lookup.reserved_block(row.reserved_block_id)
    block = reserved_blocks_by_natural_id[reserved_block_id]
    return ScheduleEntry(
        source=EntrySource.RESERVED_BLOCK,
        activity_id=block.activity_id,
        day_id=day_id,
        period_id=period_id,
        class_sections=block.class_sections,
        teacher_id=block.teacher_id,
        participant_group_id=None,
        reserved_block_id=block.id,
    )


def locked_occurrence_to_domain(row: orm.LockedOccurrence, lookup: NaturalIdLookup) -> OccurrenceKey:
    return OccurrenceKey(
        requirement_id=lookup.teaching_requirement(row.teaching_requirement_id),
        day_id=lookup.day(row.day_id),
        anchor_period_id=lookup.period(row.anchor_period_id),
    )
