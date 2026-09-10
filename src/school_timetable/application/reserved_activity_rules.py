"""Pure, framework-free business rules for the Reserved Activities
Slice A2 Reserved Activity write service.

Every function here reasons only about already-loaded, plain
`SchedulingProblem`/domain objects -- never SQLAlchemy, never a
persistence surrogate ID. This is deliberate: the same pure functions
run twice per write (an early, un-locked fast-fail check in
`ReservedActivityService`, and the authoritative, lock-protected
recheck `SqlAlchemyReservedActivityRepository` invokes against a
freshly-reloaded `SchedulingProblem` immediately before committing --
Owner Decision #36) -- reusing one implementation for both means the
two checks can never silently diverge.

"Reserved Activity" is not a new domain entity -- it is the
user-facing name for `ReservedBlock` (see `domain/blocks.py`).

Validation is layered, in this exact order, mirroring
`teacher_availability_rules.validate_replace`'s own layered discipline:

1. structural request checks (empty/duplicate collections) -- never
   need a DB lookup, bundled into one `InvalidReservedActivityError`.
2. reference existence (`special_activity_id`/`class_section_ids`/
   `teacher_id`/each slot's `day_id`/`period_id`) -- the first missing
   reference raises `UnknownReferenceError` immediately.
3. the referenced Special Activity's kind -- `NonSpecialActivityTargetError`
   if it is not `ActivityKind.CLUB`. Deliberately a dedicated error,
   never bundled into `InvalidReservedActivityError`'s diagnostics and
   never leaking `ActivityKind`/`CLUB`/`ORDINARY` in its own response
   (unlike Teaching Assignments' unrelated, unaltered
   `NON_ORDINARY_ACTIVITY_TARGET` contract).
4. semantic/collision checks -- a candidate `ReservedBlock` is
   constructed in-memory and diffed against `problem` through the
   existing `run_preflight`, exactly mirroring
   `teaching_assignment_rules.new_validation_errors`'s own pattern:
   only error codes preflight reports against the candidate but not
   against the unmodified baseline block the write, bundled into one
   `InvalidReservedActivityError`. This reuses the same
   `RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT`/`RESERVED_BLOCK_TEACHER_
   UNAVAILABLE`/`RESERVED_BLOCK_CLASS_SLOT_COLLISION`/
   `RESERVED_BLOCK_TEACHER_SLOT_COLLISION` checks `validation/
   preflight.py` already performs independently, so the two can never
   silently diverge.
"""
from __future__ import annotations

from dataclasses import replace

from school_timetable.application.errors import (
    InvalidReservedActivityError,
    NonSpecialActivityTargetError,
    ReservedActivityNotFoundError,
    UnknownReferenceError,
)
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import TimeSlot
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.validation.errors import ValidationError
from school_timetable.validation.preflight import run_preflight

# Only these preflight codes are ever surfaced by the candidate-diff
# stage (step 4 above) -- everything else preflight might newly report
# (e.g. `CLASS_OCCUPANCY_MISMATCH`, `TEACHER_OVERLOADED`) reflects
# ordinary mid-configuration incompleteness elsewhere in the school's
# setup, not a defect in this specific write, mirroring
# `teaching_assignment_rules.WARNING_ONLY_VALIDATION_CODES`'s own
# save-time validation boundary. `RESERVED_BLOCK_NON_CLUB_ACTIVITY` is
# included only for defense-in-depth symmetry -- it can never actually
# fire here, since step 3 above already raises the dedicated
# `NonSpecialActivityTargetError` before the candidate is ever built.
_RESERVED_ACTIVITY_BLOCKING_CODES = frozenset({
    "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION",
    "DUPLICATE_RESERVED_BLOCK_SLOT",
    "RESERVED_BLOCK_NON_CLUB_ACTIVITY",
    "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT",
    "RESERVED_BLOCK_TEACHER_UNAVAILABLE",
    "RESERVED_BLOCK_CLASS_SLOT_COLLISION",
    "RESERVED_BLOCK_TEACHER_SLOT_COLLISION",
})


def find_reserved_block(problem: SchedulingProblem, reserved_activity_id: str) -> ReservedBlock | None:
    return next((b for b in problem.reserved_blocks if b.id == reserved_activity_id), None)


def find_special_activity(problem: SchedulingProblem, special_activity_id: str) -> Activity | None:
    return next((a for a in problem.activities if a.id == special_activity_id), None)


def _validate_structural(
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    class_section_ids: tuple[str, ...],
    slots: tuple[tuple[str, str], ...],
) -> None:
    """Empty collections and in-request duplicates -- pure input
    hygiene, never needs `problem`. Collected into one
    `InvalidReservedActivityError`, never silently deduplicated,
    mirroring `teacher_availability_rules.validate_replace`'s own
    structural-checks-first, bundled-diagnostics discipline."""
    errors: list[ValidationError] = []

    if not class_section_ids:
        errors.append(ValidationError(
            "RESERVED_BLOCK_REQUIRES_CLASS_SECTION",
            "at least one class section is required",
            {},
        ))
    if not slots:
        errors.append(ValidationError(
            "RESERVED_BLOCK_REQUIRES_SLOT",
            "at least one slot is required",
            {},
        ))

    seen_classes: set[str] = set()
    dup_classes: set[str] = set()
    for class_id in class_section_ids:
        if class_id in seen_classes:
            dup_classes.add(class_id)
        seen_classes.add(class_id)
    for class_id in sorted(dup_classes):
        errors.append(ValidationError(
            "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION",
            f"class {class_id!r} appears more than once in this request",
            {"class_id": class_id},
        ))

    seen_slots: set[tuple[str, str]] = set()
    dup_slots: set[tuple[str, str]] = set()
    for slot in slots:
        if slot in seen_slots:
            dup_slots.add(slot)
        seen_slots.add(slot)
    for day_id, period_id in sorted(dup_slots):
        errors.append(ValidationError(
            "DUPLICATE_RESERVED_BLOCK_SLOT",
            f"slot (day={day_id!r}, period={period_id!r}) appears more than once in this request",
            {"day_id": day_id, "period_id": period_id},
        ))

    if errors:
        raise InvalidReservedActivityError(school_natural_id, academic_year_natural_id, tuple(errors))


def _validate_references(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    *,
    special_activity_id: str,
    class_section_ids: tuple[str, ...],
    teacher_id: str | None,
    slots: tuple[tuple[str, str], ...],
) -> None:
    """The first missing reference (in the fixed order: special
    activity, class sections in request order, teacher, slots in
    request order) raises `UnknownReferenceError` immediately -- never
    bundled, matching `teaching_assignment_rules.validate_create`'s
    own single-error-per-missing-reference discipline."""
    if find_special_activity(problem, special_activity_id) is None:
        raise UnknownReferenceError(
            school_natural_id, academic_year_natural_id, "special_activity", special_activity_id,
        )
    known_class_ids = {c.id for c in problem.class_sections}
    for class_id in class_section_ids:
        if class_id not in known_class_ids:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "class_section", class_id)
    if teacher_id is not None and not any(t.id == teacher_id for t in problem.teachers):
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "teacher", teacher_id)
    known_day_ids = {d.id for d in problem.days}
    known_period_ids = {p.id for p in problem.periods}
    for day_id, period_id in slots:
        if day_id not in known_day_ids:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "day", day_id)
        if period_id not in known_period_ids:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "period", period_id)


def _require_special_activity_kind(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    special_activity_id: str,
) -> None:
    """Only reachable once `_validate_references` has already confirmed
    `special_activity_id` resolves -- `UNKNOWN_REFERENCE` and
    `NON_SPECIAL_ACTIVITY_TARGET` therefore never both fire for the
    same reference."""
    activity = find_special_activity(problem, special_activity_id)
    assert activity is not None  # guaranteed by _validate_references, never actually reached otherwise
    if activity.kind != ActivityKind.CLUB:
        raise NonSpecialActivityTargetError(school_natural_id, academic_year_natural_id, special_activity_id)


def _candidate_diff_errors(
    problem: SchedulingProblem, candidate: SchedulingProblem,
) -> tuple[ValidationError, ...]:
    """Every `ValidationError` code preflight reports against
    `candidate` but not against `problem`, filtered to the codes this
    write surface actually blocks on -- mirrors
    `teaching_assignment_rules.new_validation_errors` exactly."""
    baseline_codes = {e.code for e in run_preflight(problem)}
    return tuple(
        e for e in run_preflight(candidate)
        if e.code not in baseline_codes and e.code in _RESERVED_ACTIVITY_BLOCKING_CODES
    )


def validate_create(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    reserved_activity_id: str,
    *,
    special_activity_id: str,
    class_section_ids: tuple[str, ...],
    teacher_id: str | None,
    slots: tuple[tuple[str, str], ...],
) -> None:
    _validate_structural(
        school_natural_id, academic_year_natural_id, class_section_ids=class_section_ids, slots=slots,
    )
    _validate_references(
        problem, school_natural_id, academic_year_natural_id,
        special_activity_id=special_activity_id, class_section_ids=class_section_ids,
        teacher_id=teacher_id, slots=slots,
    )
    _require_special_activity_kind(problem, school_natural_id, academic_year_natural_id, special_activity_id)

    candidate_block = ReservedBlock(
        id=reserved_activity_id,
        name="",  # never inspected by preflight; the repository computes the real derived name.
        activity_id=special_activity_id,
        class_sections=class_section_ids,
        slots=tuple(TimeSlot(day_id, period_id) for day_id, period_id in slots),
        teacher_id=teacher_id,
    )
    candidate = replace(problem, reserved_blocks=problem.reserved_blocks + (candidate_block,))
    new_errors = _candidate_diff_errors(problem, candidate)
    if new_errors:
        raise InvalidReservedActivityError(school_natural_id, academic_year_natural_id, new_errors)


def validate_update(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    reserved_activity_id: str,
    *,
    special_activity_id: str,
    class_section_ids: tuple[str, ...],
    teacher_id: str | None,
    slots: tuple[tuple[str, str], ...],
) -> None:
    existing = find_reserved_block(problem, reserved_activity_id)
    if existing is None:
        raise ReservedActivityNotFoundError(school_natural_id, academic_year_natural_id, reserved_activity_id)

    _validate_structural(
        school_natural_id, academic_year_natural_id, class_section_ids=class_section_ids, slots=slots,
    )
    _validate_references(
        problem, school_natural_id, academic_year_natural_id,
        special_activity_id=special_activity_id, class_section_ids=class_section_ids,
        teacher_id=teacher_id, slots=slots,
    )
    _require_special_activity_kind(problem, school_natural_id, academic_year_natural_id, special_activity_id)

    candidate_block = ReservedBlock(
        id=reserved_activity_id,
        name="",
        activity_id=special_activity_id,
        class_sections=class_section_ids,
        slots=tuple(TimeSlot(day_id, period_id) for day_id, period_id in slots),
        teacher_id=teacher_id,
    )
    # The target block is REPLACED, never duplicated -- `candidate`
    # never contains two entries sharing `reserved_activity_id`, so the
    # collision scan inside `run_preflight` can never see the block
    # collide with its own prior self.
    candidate = replace(
        problem,
        reserved_blocks=tuple(
            candidate_block if b.id == reserved_activity_id else b for b in problem.reserved_blocks
        ),
    )
    new_errors = _candidate_diff_errors(problem, candidate)
    if new_errors:
        raise InvalidReservedActivityError(school_natural_id, academic_year_natural_id, new_errors)


def validate_delete(
    problem: SchedulingProblem,
    school_natural_id: str,
    academic_year_natural_id: str,
    reserved_activity_id: str,
) -> None:
    if find_reserved_block(problem, reserved_activity_id) is None:
        raise ReservedActivityNotFoundError(school_natural_id, academic_year_natural_id, reserved_activity_id)
    # No "in use" check: a ReservedBlock is a leaf configuration
    # aggregate -- the only FK referencing `reserved_block.id` besides
    # its own CASCADE children is `schedule_entry.reserved_block_id`
    # (RESTRICT), which is structurally unreachable here since the
    # configuration lock already rejects this delete outright once any
    # Schedule exists.
