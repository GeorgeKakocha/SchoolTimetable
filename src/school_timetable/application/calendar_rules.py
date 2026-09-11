"""Pure, framework-free business rules for Calendar A's Day/Period write
service.

Every function here reasons only about an already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same rule functions
run twice per write (an early, un-locked fast-fail check in
`CalendarService`, and the authoritative, lock-protected recheck
`persistence.calendar_repository` invokes against a freshly-reloaded
`SchedulingProblem` immediately before committing -- Owner Decision
#36) -- reusing one pure implementation for both means the two checks
can never silently diverge.

`Day`/`Period` remain the existing `domain.calendar` entities -- Calendar
A only adds the missing catalog write surface over them, plus two new
optional `Period.start_time`/`end_time` fields. `block_id` is never a
public write input: callers submit `starts_new_block: bool` instead,
and `domain.calendar.recompute_block_ids` deterministically derives
every Period's internal `block_id` from the whole ordered sequence.
"""
from __future__ import annotations

from datetime import time

from school_timetable.application.errors import (
    DayInUseError,
    DayNotFoundError,
    DuplicateDayError,
    DuplicatePeriodError,
    InvalidDayError,
    InvalidPeriodError,
    PeriodInUseError,
    PeriodNotFoundError,
    PeriodReorderBlockedError,
)
from school_timetable.domain.calendar import Day, Period, clock_time_overlaps
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError

MoveDirection = str
"""`"up"` or `"down"` -- a plain, transient API-layer concept, never
persisted, so never a domain `Enum` (unlike `AvailabilityStatus`/
`BlockPolicyMode`, which are persisted, CheckConstraint'd column
values)."""


# == Shared name/lookup helpers ===============================================


def normalize_name(name: str) -> str:
    """Trims outer whitespace only -- never restricts alphabet, script,
    punctuation, or length, matching every other catalog's
    `normalize_name` exactly."""
    return name.strip()


def find_day(problem: SchedulingProblem, day_id: str) -> Day | None:
    return next((d for d in problem.days if d.id == day_id), None)


def find_period(problem: SchedulingProblem, period_id: str) -> Period | None:
    return next((p for p in problem.periods if p.id == period_id), None)


def find_duplicate_day(problem: SchedulingProblem, name: str, *, exclude_day_id: str | None = None) -> Day | None:
    for day in problem.days:
        if day.id == exclude_day_id:
            continue
        if day.name == name:
            return day
    return None


def find_duplicate_period(
    problem: SchedulingProblem, name: str, *, exclude_period_id: str | None = None,
) -> Period | None:
    for period in problem.periods:
        if period.id == exclude_period_id:
            continue
        if period.name == name:
            return period
    return None


# == Delete-reference detection ===============================================


def find_day_references(problem: SchedulingProblem, day_id: str) -> tuple[str, ...]:
    """Every current pre-schedule configuration entity kind referencing
    `day_id`, in deterministic order. Historical `ScheduleEntry`/
    `LockedOccurrence` rows are never inspected here -- Decision #35
    already forbids reaching this check at all once any `Schedule`
    exists for the year."""
    kinds: list[str] = []
    if any(a.day_id == day_id for a in problem.teacher_availabilities):
        kinds.append("TEACHER_AVAILABILITY")
    if any(slot.day_id == day_id for block in problem.reserved_blocks for slot in block.slots):
        kinds.append("RESERVED_BLOCK")
    if any(fp.slot.day_id == day_id for fp in problem.fixed_placements):
        kinds.append("FIXED_PLACEMENT")
    return tuple(kinds)


def find_period_references(problem: SchedulingProblem, period_id: str) -> tuple[str, ...]:
    """Every current pre-schedule configuration entity kind directly
    referencing `period_id` *by ID* -- mirrors `find_day_references`
    exactly. Does NOT include `TIME_PREFERENCE`: `TimePreference`
    stores raw `Period.index` integers, never a `Period.id`, so that
    hazard is handled separately by `validate_period_delete`'s own
    index-drift-safety logic, not by this direct-reference lookup."""
    kinds: list[str] = []
    if any(a.period_id == period_id for a in problem.teacher_availabilities):
        kinds.append("TEACHER_AVAILABILITY")
    if any(slot.period_id == period_id for block in problem.reserved_blocks for slot in block.slots):
        kinds.append("RESERVED_BLOCK")
    if any(fp.slot.period_id == period_id for fp in problem.fixed_placements):
        kinds.append("FIXED_PLACEMENT")
    return tuple(kinds)


# == TimePreference index-drift safety (section 12/13) ========================


def _referenced_period_indexes(problem: SchedulingProblem) -> set[int]:
    indexes: set[int] = set()
    for requirement in problem.teaching_requirements:
        for preference in requirement.time_preferences:
            indexes.update(preference.preferred_periods)
    return indexes


def any_time_preferences_exist(problem: SchedulingProblem) -> bool:
    return any(requirement.time_preferences for requirement in problem.teaching_requirements)


# == Day validation ============================================================


def validate_day_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    if name == "":
        raise InvalidDayError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_DAY_NAME", "name is blank after trimming"),),
        )


def validate_day_create(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, *, name: str,
) -> None:
    validate_day_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate_day(problem, name)
    if duplicate is not None:
        raise DuplicateDayError(school_natural_id, academic_year_natural_id, name)


def validate_day_update(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, day_id: str, *, name: str,
) -> None:
    if find_day(problem, day_id) is None:
        raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_id)
    validate_day_name(school_natural_id, academic_year_natural_id, name)
    duplicate = find_duplicate_day(problem, name, exclude_day_id=day_id)
    if duplicate is not None:
        raise DuplicateDayError(school_natural_id, academic_year_natural_id, name)


def validate_day_delete(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, day_id: str,
) -> None:
    if find_day(problem, day_id) is None:
        raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_id)
    if len(problem.days) <= 1:
        raise InvalidDayError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("NO_CALENDAR_DAYS", "the Academic Year must retain at least one Day"),),
        )
    referenced_by = find_day_references(problem, day_id)
    if referenced_by:
        raise DayInUseError(school_natural_id, academic_year_natural_id, day_id, referenced_by)


def validate_day_move(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    day_id: str,
    direction: MoveDirection,
) -> None:
    """No `TimePreference`-style hazard exists for `Day` -- nothing in
    this schema stores a raw `Day.index` reference (every Day reference
    is by `day_id`), so Day reorder is permitted subject only to the
    ordinary "already at the edge" boundary check."""
    day = find_day(problem, day_id)
    if day is None:
        raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_id)
    days_sorted = sorted(problem.days, key=lambda d: d.index)
    position = next(i for i, d in enumerate(days_sorted) if d.id == day_id)
    if direction == "up" and position == 0:
        raise InvalidDayError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("DAY_ALREADY_AT_TOP", "this Day is already first and cannot move up"),),
        )
    if direction == "down" and position == len(days_sorted) - 1:
        raise InvalidDayError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("DAY_ALREADY_AT_BOTTOM", "this Day is already last and cannot move down"),),
        )


# == Period validation =========================================================


def validate_period_name(school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
    if name == "":
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("BLANK_PERIOD_NAME", "name is blank after trimming"),),
        )


def validate_time_pair(
    school_natural_id: str, academic_year_natural_id: str, start_time: time | None, end_time: time | None,
) -> None:
    """Section 8: both null, or both present -- never only one. If both
    are present, `start_time < end_time` (equal is also rejected --
    a zero-length period is never valid)."""
    if (start_time is None) != (end_time is None):
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError(
                "PERIOD_TIME_PAIR_INCOMPLETE", "start_time and end_time must both be set, or both be null",
            ),),
        )
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("PERIOD_TIME_ORDER_INVALID", "start_time must be strictly before end_time"),),
        )


def validate_period_clock_order(
    school_natural_id: str, academic_year_natural_id: str, candidate_periods: tuple[Period, ...],
) -> None:
    """Reuses `domain.calendar.clock_time_overlaps` -- the exact same
    pure check `validation.preflight` runs -- against a candidate full
    Period sequence (the whole catalog with the one being written
    already substituted/inserted/removed), so a write-time rejection
    and a preflight rejection can never disagree."""
    overlaps = clock_time_overlaps(candidate_periods)
    if overlaps:
        a, b = overlaps[0]
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError(
                "PERIOD_CLOCK_TIME_OVERLAP",
                f"Period {a.id!r} ends at {a.end_time} which is after Period {b.id!r} starts at {b.start_time}",
                {"period_id": a.id, "next_period_id": b.id},
            ),),
        )


def validate_period_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    name: str,
    start_time: time | None,
    end_time: time | None,
    candidate_periods: tuple[Period, ...],
) -> None:
    validate_period_name(school_natural_id, academic_year_natural_id, name)
    validate_time_pair(school_natural_id, academic_year_natural_id, start_time, end_time)
    duplicate = find_duplicate_period(problem, name)
    if duplicate is not None:
        raise DuplicatePeriodError(school_natural_id, academic_year_natural_id, name)
    validate_period_clock_order(school_natural_id, academic_year_natural_id, candidate_periods)


def validate_period_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    period_id: str,
    *,
    name: str,
    start_time: time | None,
    end_time: time | None,
    candidate_periods: tuple[Period, ...],
) -> None:
    if find_period(problem, period_id) is None:
        raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_id)
    validate_period_name(school_natural_id, academic_year_natural_id, name)
    validate_time_pair(school_natural_id, academic_year_natural_id, start_time, end_time)
    duplicate = find_duplicate_period(problem, name, exclude_period_id=period_id)
    if duplicate is not None:
        raise DuplicatePeriodError(school_natural_id, academic_year_natural_id, name)
    validate_period_clock_order(school_natural_id, academic_year_natural_id, candidate_periods)


def validate_period_delete(
    problem: SchedulingProblem, school_natural_id: str, academic_year_natural_id: str, period_id: str,
) -> None:
    """Section 13's index-drift-safety rule, implemented *precisely*
    (a stronger guarantee than the task's "conservative is acceptable"
    fallback, and just as simple): collect every index any
    `TimePreference` in the Academic Year actually refers to. Deleting
    `period_id` (at position `i`) is unsafe iff that set contains `i`
    itself (a preference names this exact Period) OR any index strictly
    greater than `i` (some later Period's index would shift down to
    fill the gap, silently redirecting whatever preference named it).
    Deleting the very last Period therefore only ever needs the first
    half of this check -- exactly section 13(C)'s stated rule."""
    period = find_period(problem, period_id)
    if period is None:
        raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_id)

    referenced_by = list(find_period_references(problem, period_id))

    if period.is_instructional and sum(1 for p in problem.periods if p.is_instructional) <= 1:
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError(
                "NO_INSTRUCTIONAL_PERIODS", "the Academic Year must retain at least one instructional Period",
            ),),
        )

    referenced_indexes = _referenced_period_indexes(problem)
    if referenced_indexes and (period.index in referenced_indexes or any(i > period.index for i in referenced_indexes)):
        referenced_by.append("TIME_PREFERENCE")

    if referenced_by:
        raise PeriodInUseError(school_natural_id, academic_year_natural_id, period_id, tuple(referenced_by))


def validate_period_move(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    period_id: str,
    direction: MoveDirection,
) -> None:
    """Section 12: a blanket block whenever *any* `TimePreference`
    exists anywhere in the Academic Year -- deliberately simpler and
    more conservative than `validate_period_delete`'s precise
    reachability check, matching the task's own explicit instruction
    for reorder specifically."""
    period = find_period(problem, period_id)
    if period is None:
        raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_id)
    if any_time_preferences_exist(problem):
        raise PeriodReorderBlockedError(school_natural_id, academic_year_natural_id, period_id)
    periods_sorted = sorted(problem.periods, key=lambda p: p.index)
    position = next(i for i, p in enumerate(periods_sorted) if p.id == period_id)
    if direction == "up" and position == 0:
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("PERIOD_ALREADY_AT_TOP", "this Period is already first and cannot move up"),),
        )
    if direction == "down" and position == len(periods_sorted) - 1:
        raise InvalidPeriodError(
            school_natural_id, academic_year_natural_id,
            (ValidationError("PERIOD_ALREADY_AT_BOTTOM", "this Period is already last and cannot move down"),),
        )
