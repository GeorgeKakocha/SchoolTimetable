"""Manual schedule editing: validate-then-apply moves, and locking
(Phase 2C).

This is the application layer sitting between the plain domain model
(``domain.schedule.Schedule``) and the solver: it answers "is this manual
edit allowed?" and, if so, performs it -- without ever mutating a
``Schedule`` before an explicit ``MoveValidationResult`` says it may.

## Two runtime safety guarantees (hardened after a pre-commit audit)

1. **The input `Schedule` is never trusted blindly.** `validate_move`
   independently re-verifies (via the same `verification.verifier` every
   generated schedule must pass) that the schedule handed to it is
   actually HARD-valid before reasoning about a move at all, rejecting
   with `INVALID_SCHEDULE` otherwise. `find_logical_occurrence` -- a
   public helper other callers (locking, `reoptimize`) also use directly
   -- additionally verifies on its own that a REQUIRED block (or a formed
   PREFERRED double) it is about to treat as one occurrence is genuinely
   a consecutive same-`block_id` run, raising `EditingError(code="INVALID_SCHEDULE")`
   rather than silently grouping a malformed schedule as if it were valid.
2. **A validated plan can never be blindly applied to a schedule that has
   since changed.** `apply_move` re-runs `validate_move` against
   whatever schedule it is actually given, using the original
   `MoveIntent`, and only proceeds if that fresh validation is still
   allowed *and* resolves to an equivalent `MovePlan`. A stale plan is
   rejected with `EditingError(code="STALE_MOVE_PLAN")`, never silently
   applied -- see the "stale MovePlan" section below and
   `docs/SCHEDULE_EDITING.md`.

## Why a move is a swap, not a plain relocation

Full class occupancy is a HARD constraint: every instructional slot for
every class is always filled by exactly one activity. A literal
"relocate occurrence X from A to B" would leave A empty unless something
else fills it -- which nothing does by definition of a single move -- so
a plain relocation can never produce a schedule that still satisfies full
occupancy. Since an accepted move must produce a schedule that passes the
same independent verifier as any generated schedule (this is required,
not optional), the only occupancy-preserving single-step edit is a
**swap**: the moved occurrence and whatever currently occupies the target
window (for the same classes) exchange places. This is a deliberate,
documented design decision resolving a real tension in the brief, not
guessed behavior -- see ``docs/SCHEDULE_EDITING.md``.

## Logical occurrence

The unit a move/lock operates on is a *logical occurrence*: a
requirement's block of periods on one day (which may be just one period),
expanded to include every synchronized split-group sibling's matching
periods on that same day.

Reconstructing "which periods on this day belong to the same block" from
an already-valid schedule is unambiguous only for the block policies
whose CP-SAT encoding actually enforces a day-cap: REQUIRED (always) and
PREFERRED with a formed double (only while that double actually exists).
For those, a requirement provably has at most one contiguous run per day,
so every period it uses that day belongs to the same block. FLEXIBLE
requirements have **no such guarantee** -- the model_builder places no cap
on how many, or how scattered, a FLEXIBLE requirement's periods on one day
can be, and in practice they routinely are scattered (e.g. a class's
FLEXIBLE Science slots landing on 4 different periods of the same
Wednesday). Treating "same requirement, same day" as one block there would
silently glue together periods that were never meant to move together.
So: for FLEXIBLE (and PREFERRED without a formed double, which the model
also does not day-cap), each period is its own independent occurrence,
identified by ``(requirement_id, day_id, period_id)`` -- a period must
always be supplied to disambiguate, exactly as the move API already
requires "(source day, source period)".
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from school_timetable.domain.calendar import period_runs
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.verification.verifier import _check_required_block_patterns
from school_timetable.verification.verifier import verify as verify_schedule


class EditingError(RuntimeError):
    """An editing operation could not even be attempted (unknown
    requirement/day, a malformed logical occurrence, or an otherwise
    HARD-invalid schedule). This is distinct from
    ``MoveValidationResult(allowed=False, ...)``, which represents a
    well-formed request that HARD rules reject.

    ``code`` is an optional stable machine-readable reason (e.g.
    ``"INVALID_SCHEDULE"``, ``"STALE_MOVE_PLAN"``) so callers can branch on
    it without parsing the message text.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Logical occurrence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OccurrenceMember:
    requirement_id: str
    day_id: str
    period_id: str


@dataclass(frozen=True)
class LogicalOccurrence:
    """One movable/lockable unit: a requirement's block of periods on one
    day, plus every synchronized split-group sibling's matching periods.
    """

    day_id: str
    requirement_ids: tuple[str, ...]
    period_ids: tuple[str, ...]
    members: tuple[OccurrenceMember, ...]
    class_ids: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.period_ids)


def _assert_consecutive_same_block_run(index: ProblemIndex, period_ids: tuple[str, ...], context: str) -> None:
    """Defense-in-depth for a public helper: raise loudly rather than
    silently group periods that are not genuinely one consecutive,
    same-block_id run. A REQUIRED block (or a formed PREFERRED double) can
    only ever look like this if it came from a genuinely valid schedule --
    if it doesn't, the input violated the "already valid" precondition,
    and grouping it anyway would let a malformed schedule masquerade as a
    normal logical occurrence.
    """
    if len(period_ids) <= 1:
        return
    ordered = sorted((index.periods_by_id[pid] for pid in period_ids), key=lambda p: p.index)
    for a, b in zip(ordered, ordered[1:]):
        if b.index != a.index + 1 or a.block_id != b.block_id:
            raise EditingError(
                f"Malformed block for {context}: periods {period_ids!r} are not a single "
                "consecutive same-block_id run -- the input schedule is expected to already be "
                "HARD-valid",
                code="INVALID_SCHEDULE",
            )


def _block_periods_for(
    req: TeachingRequirement, day_id: str, period_id: str, index: ProblemIndex, schedule: Schedule,
) -> tuple[str, ...]:
    """The periods (sorted) that form the same logical block as
    (req, day_id, period_id), for this one requirement only (no split
    expansion yet).

    Grouping "every period this requirement uses on this day" is only
    ever correct for REQUIRED (always) and a formed PREFERRED double
    (only while that double genuinely exists) -- both are the only
    policies whose CP-SAT encoding day-caps a requirement, so both are
    verified here to genuinely be one consecutive same-block_id run
    rather than trusted blindly. FLEXIBLE (and PREFERRED without a formed
    double) is always per-period; that behavior is unchanged.
    """
    day_periods = tuple(sorted(
        (e.period_id for e in schedule.entries if e.requirement_id == req.id and e.day_id == day_id),
        key=lambda pid: index.periods_by_id[pid].index,
    ))
    if period_id not in day_periods:
        raise EditingError(
            f"Requirement {req.id!r} has no occurrence at ({day_id!r}, {period_id!r})",
            code="OCCURRENCE_NOT_FOUND",
        )

    context = f"requirement {req.id!r} on day {day_id!r}"

    if req.block_policy.mode == BlockPolicyMode.REQUIRED:
        _assert_consecutive_same_block_run(index, day_periods, context)
        return day_periods

    if req.block_policy.mode == BlockPolicyMode.PREFERRED and req.block_policy.has_double:
        if len(day_periods) > 2:
            raise EditingError(
                f"Requirement {req.id!r} has {len(day_periods)} periods on day {day_id!r}, which "
                "exceeds what a PREFERRED double-lesson policy can ever produce -- the input "
                "schedule is expected to already be HARD-valid",
                code="INVALID_SCHEDULE",
            )
        if len(day_periods) == 2:
            _assert_consecutive_same_block_run(index, day_periods, context)
        return day_periods

    return (period_id,)


def find_logical_occurrence(
    problem: SchedulingProblem, index: ProblemIndex, schedule: Schedule,
    requirement_id: str, day_id: str, period_id: str,
) -> LogicalOccurrence:
    req = index.requirements_by_id.get(requirement_id)
    if req is None:
        raise EditingError(f"Unknown requirement {requirement_id!r}", code="OCCURRENCE_NOT_FOUND")

    own_periods = _block_periods_for(req, day_id, period_id, index, schedule)
    req_ids = list(index.split_groups[req.split_group_id]) if req.split_group_id else [requirement_id]

    # Split synchronization (x[req_a,d,p] == x[req_b,d,p] for every single
    # (d,p), unconditionally) guarantees every sibling has an entry at
    # exactly the same periods the primary requirement does on this day --
    # regardless of either's own block policy. So the primary's own_periods
    # (already resolved above, respecting ITS policy) applies to every
    # sibling too; we only need to confirm the schedule actually shows that
    # (defensive -- the input is assumed already valid).
    members: list[OccurrenceMember] = []
    for rid in req_ids:
        sibling_periods = {
            e.period_id for e in schedule.entries if e.requirement_id == rid and e.day_id == day_id
        }
        if not set(own_periods).issubset(sibling_periods):
            raise EditingError(
                f"Split branch {rid!r} is not synchronized with {requirement_id!r} at "
                f"({day_id!r}, periods {own_periods!r}) in the given schedule -- the input "
                "schedule is expected to already be valid",
                code="INVALID_SCHEDULE",
            )
        members.extend(OccurrenceMember(rid, day_id, pid) for pid in own_periods)

    group = index.participant_groups_by_id[req.participant_group_id]

    return LogicalOccurrence(
        day_id=day_id,
        requirement_ids=tuple(req_ids),
        period_ids=own_periods,
        members=tuple(members),
        class_ids=tuple(group.class_sections),
    )


def _window_starting_at(index: ProblemIndex, start_period_id: str, length: int) -> tuple[str, ...] | None:
    """The `length`-period window starting at `start_period_id`, if it
    stays entirely within one structural run (never crosses a break)."""
    start = index.periods_by_id.get(start_period_id)
    if start is None:
        return None
    for run in period_runs(index.instructional_periods_sorted):
        if start in run:
            start_idx = run.index(start)
            if start_idx + length > len(run):
                return None
            return tuple(p.id for p in run[start_idx:start_idx + length])
    return None


# ---------------------------------------------------------------------------
# Move validation / application
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MoveViolation:
    code: str
    message: str


@dataclass(frozen=True)
class MoveIntent:
    """The original move request. Retained on every ``MoveValidationResult``
    (allowed or not) so ``apply_move`` can re-run ``validate_move`` against
    whatever schedule it is actually about to modify, rather than trusting
    a plan computed against a schedule that may since have changed -- see
    "stale MovePlan safety" in ``docs/SCHEDULE_EDITING.md``.
    """

    requirement_id: str
    source_day_id: str
    source_period_id: str
    target_day_id: str
    target_period_id: str


@dataclass(frozen=True)
class MovePlan:
    """What ``validate_move`` computed a move would do. Never trusted on
    its own by ``apply_move`` -- only used, after fresh re-validation, to
    confirm the current schedule resolves the same ``MoveIntent`` to an
    equivalent plan."""

    removed_entries: tuple[ScheduleEntry, ...]
    added_entries: tuple[ScheduleEntry, ...]


@dataclass(frozen=True)
class MoveValidationResult:
    allowed: bool
    intent: MoveIntent
    violations: tuple[MoveViolation, ...] = ()
    plan: MovePlan | None = None


def _fixed_slot_set(problem: SchedulingProblem) -> set[tuple[str, str, str]]:
    return {(fp.requirement_id, fp.slot.day_id, fp.slot.period_id) for fp in problem.fixed_placements}


def _occurrence_entries(schedule: Schedule, occ: LogicalOccurrence) -> tuple[ScheduleEntry, ...]:
    member_keys = {(m.requirement_id, m.day_id, m.period_id) for m in occ.members}
    return tuple(
        e for e in schedule.entries
        if e.requirement_id is not None and (e.requirement_id, e.day_id, e.period_id) in member_keys
    )


def _rebuild_entry(entry: ScheduleEntry, day_id: str, period_id: str) -> ScheduleEntry:
    return dataclasses.replace(entry, day_id=day_id, period_id=period_id)


def _reject(intent: MoveIntent, code: str, message: str) -> MoveValidationResult:
    return MoveValidationResult(allowed=False, intent=intent, violations=(MoveViolation(code, message),))


def validate_move(
    problem: SchedulingProblem,
    schedule: Schedule,
    requirement_id: str,
    source_day_id: str,
    source_period_id: str,
    target_day_id: str,
    target_period_id: str,
    index: ProblemIndex | None = None,
) -> MoveValidationResult:
    index = index or ProblemIndex(problem)
    intent = MoveIntent(requirement_id, source_day_id, source_period_id, target_day_id, target_period_id)

    # The editing application layer must never trust an externally supplied
    # Schedule: reuse the same independent verifier every generated
    # schedule must pass, rather than duplicating HARD rules here. If the
    # input itself isn't HARD-valid, no move can be safely reasoned about
    # against it.
    schedule_report = verify_schedule(problem, schedule.entries)
    if not schedule_report.passed:
        return _reject(
            intent,
            "INVALID_SCHEDULE",
            "The current schedule is not HARD-valid, so no move can be safely evaluated against it: "
            + "; ".join(schedule_report.violations),
        )

    try:
        source_occ = find_logical_occurrence(
            problem, index, schedule, requirement_id, source_day_id, source_period_id,
        )
    except EditingError as exc:
        return _reject(intent, exc.code or "OCCURRENCE_NOT_FOUND", str(exc))

    fixed_slots = _fixed_slot_set(problem)
    for m in source_occ.members:
        if (m.requirement_id, m.day_id, m.period_id) in fixed_slots:
            return _reject(
                intent,
                "FIXED_PLACEMENT",
                f"Requirement {m.requirement_id!r} is fixed at ({m.day_id!r}, {m.period_id!r}) "
                "and cannot be manually moved",
            )

    # Locked source occurrences cannot be manually moved either -- a lock
    # is a stronger promise than "no active edit happens to touch it".
    source_anchor = source_occ.period_ids[0]
    for rid in source_occ.requirement_ids:
        if schedule.is_locked(OccurrenceKey(rid, source_day_id, source_anchor)):
            return _reject(
                intent,
                "LOCKED_OCCURRENCE",
                f"Requirement {rid!r}'s occurrence on {source_day_id!r} is locked and cannot be moved",
            )

    target_window = _window_starting_at(index, target_period_id, source_occ.length)
    if target_window is None:
        return _reject(
            intent,
            "TARGET_WINDOW_INVALID",
            f"No valid {source_occ.length}-period window starting at {target_period_id!r} "
            "without crossing a structural break or running past the calendar",
        )

    if target_day_id == source_day_id and target_window == source_occ.period_ids:
        return MoveValidationResult(allowed=True, intent=intent, violations=(), plan=MovePlan((), ()))

    # Identify what currently occupies the target window for the same classes.
    occupant_req_ids: set[str] = set()
    occupant_reserved_block = False
    for pid in target_window:
        for e in schedule.entries:
            if e.day_id != target_day_id or e.period_id != pid:
                continue
            if not any(c in source_occ.class_ids for c in e.class_sections):
                continue
            if e.source == EntrySource.RESERVED_BLOCK:
                occupant_reserved_block = True
            elif e.requirement_id is not None:
                occupant_req_ids.add(e.requirement_id)

    if occupant_reserved_block:
        return _reject(
            intent,
            "RESERVED_BLOCK_CONFLICT",
            f"Target slot(s) on {target_day_id!r} are occupied by a reserved/club block for "
            f"class(es) {source_occ.class_ids!r}",
        )

    if len(occupant_req_ids) != 1:
        return _reject(
            intent,
            "TARGET_OCCUPANT_INCOMPATIBLE",
            f"Target window {target_window!r} on {target_day_id!r} is not occupied by exactly one "
            f"swappable occurrence for class(es) {source_occ.class_ids!r} (found {len(occupant_req_ids)})",
        )

    target_req_id = next(iter(occupant_req_ids))
    try:
        target_occ = find_logical_occurrence(
            problem, index, schedule, target_req_id, target_day_id, target_window[0],
        )
    except EditingError as exc:
        raise EditingError(f"Internal inconsistency identifying target occupant: {exc}") from exc

    if set(target_occ.period_ids) != set(target_window):
        return _reject(
            intent,
            "REQUIRED_BLOCK_VIOLATION",
            f"Target window {target_window!r} only partially overlaps requirement "
            f"{target_req_id!r}'s block {target_occ.period_ids!r} -- cannot split a block",
        )

    if set(target_occ.class_ids) != set(source_occ.class_ids):
        return _reject(
            intent,
            "CLASS_OCCUPANCY_CONFLICT",
            f"Target occupant {target_req_id!r} covers classes {target_occ.class_ids!r}, "
            f"not the same class(es) {source_occ.class_ids!r} as the moved occurrence",
        )

    for m in target_occ.members:
        if (m.requirement_id, m.day_id, m.period_id) in fixed_slots:
            return _reject(
                intent,
                "FIXED_PLACEMENT",
                f"Target occupant requirement {m.requirement_id!r} is fixed at "
                f"({m.day_id!r}, {m.period_id!r}) and cannot be displaced",
            )
    target_anchor = target_occ.period_ids[0]
    for rid in target_occ.requirement_ids:
        if schedule.is_locked(OccurrenceKey(rid, target_day_id, target_anchor)):
            return _reject(
                intent,
                "LOCKED_OCCURRENCE",
                f"Target occupant requirement {rid!r}'s occurrence on {target_day_id!r} is locked "
                "and cannot be displaced",
            )

    # Build the hypothetical swapped entries and check HARD rules.
    plan = _build_swap_plan(source_occ, target_occ, target_day_id, target_window, source_day_id, schedule)
    violations = _check_swap_hard_rules(problem, index, schedule, plan, source_occ, target_occ)
    if violations:
        return MoveValidationResult(allowed=False, intent=intent, violations=tuple(violations))

    return MoveValidationResult(allowed=True, intent=intent, violations=(), plan=plan)


def _build_swap_plan(
    source_occ: LogicalOccurrence,
    target_occ: LogicalOccurrence,
    target_day_id: str,
    target_window: tuple[str, ...],
    source_day_id: str,
    schedule: Schedule,
) -> MovePlan:
    source_entries = _occurrence_entries(schedule, source_occ)
    target_entries = _occurrence_entries(schedule, target_occ)

    # Positional period correspondence: i-th old period -> i-th new period.
    source_period_map = dict(zip(source_occ.period_ids, target_window))
    target_period_map = dict(zip(target_occ.period_ids, source_occ.period_ids))

    added = tuple(
        _rebuild_entry(e, target_day_id, source_period_map[e.period_id]) for e in source_entries
    ) + tuple(
        _rebuild_entry(e, source_day_id, target_period_map[e.period_id]) for e in target_entries
    )
    return MovePlan(removed_entries=source_entries + target_entries, added_entries=added)


def _check_swap_hard_rules(
    problem: SchedulingProblem,
    index: ProblemIndex,
    schedule: Schedule,
    plan: MovePlan,
    source_occ: LogicalOccurrence,
    target_occ: LogicalOccurrence,
) -> list[MoveViolation]:
    violations: list[MoveViolation] = []

    removed_keys = {(e.requirement_id, e.day_id, e.period_id) for e in plan.removed_entries}
    rest = [
        e for e in schedule.entries
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) not in removed_keys
    ]

    for new_entry in plan.added_entries:
        # Teacher UNAVAILABLE.
        if new_entry.teacher_id is not None:
            status = index.get_availability(new_entry.teacher_id, new_entry.day_id, new_entry.period_id)
            if status == AvailabilityStatus.UNAVAILABLE:
                violations.append(MoveViolation(
                    "TEACHER_UNAVAILABLE",
                    f"Teacher {new_entry.teacher_id!r} is UNAVAILABLE at "
                    f"({new_entry.day_id!r}, {new_entry.period_id!r})",
                ))
            # Teacher collision against the rest of the schedule.
            for other in rest:
                if (other.teacher_id == new_entry.teacher_id and other.day_id == new_entry.day_id
                        and other.period_id == new_entry.period_id):
                    violations.append(MoveViolation(
                        "TEACHER_CONFLICT",
                        f"Teacher {new_entry.teacher_id!r} is already teaching "
                        f"{other.activity_id!r} at ({new_entry.day_id!r}, {new_entry.period_id!r})",
                    ))

        # Participant-group collision.
        if new_entry.participant_group_id is not None:
            for other in rest:
                if (other.participant_group_id == new_entry.participant_group_id
                        and other.day_id == new_entry.day_id and other.period_id == new_entry.period_id):
                    violations.append(MoveViolation(
                        "GROUP_CONFLICT",
                        f"Participant group {new_entry.participant_group_id!r} already has an "
                        f"activity at ({new_entry.day_id!r}, {new_entry.period_id!r})",
                    ))

        # Resource capacity.
        if new_entry.resource_id is not None:
            capacity = index.resources_by_id[new_entry.resource_id].capacity
            count = 1 + sum(
                1 for other in rest
                if other.resource_id == new_entry.resource_id and other.day_id == new_entry.day_id
                and other.period_id == new_entry.period_id
            )
            if count > capacity:
                violations.append(MoveViolation(
                    "RESOURCE_CAPACITY",
                    f"Resource {new_entry.resource_id!r} would be used {count} times at "
                    f"({new_entry.day_id!r}, {new_entry.period_id!r}), capacity is {capacity}",
                ))

    # REQUIRED block-pattern integrity, for the whole hypothetical
    # schedule -- not just the swapped occurrences' own footprints.
    # `validate_move`'s earlier target-window/partial-overlap checks only
    # ever look at the *target* window being swapped into; they cannot by
    # themselves catch a swap that leaves a REQUIRED requirement's OTHER,
    # untouched periods newly stacked onto the same day as the moved one
    # (e.g. a single relocated onto the day that requirement's own
    # REQUIRED double already occupies), which silently breaks that
    # requirement's per-day-count/distinct-days pattern without
    # violating any single-window check. Reused directly from the
    # independent verifier -- the exact same pure check `reoptimize.py`
    # already imports this way -- rather than duplicating its logic or
    # running the full `verify()` (which would also re-check teacher/
    # group/occupancy rules this function already covers itself).
    hypothetical = rest + list(plan.added_entries)
    for message in _check_required_block_patterns(problem, index, hypothetical):
        violations.append(MoveViolation("REQUIRED_BLOCK_VIOLATION", message))

    # max_periods_per_day for every requirement involved in the swap, on
    # both of their new days.
    for occ in (source_occ, target_occ):
        for rid in occ.requirement_ids:
            req = index.requirements_by_id[rid]
            max_per_day = req.distribution_policy.max_periods_per_day
            if max_per_day is None:
                continue
            counts: dict[str, int] = {}
            for e in hypothetical:
                if e.requirement_id == rid:
                    counts[e.day_id] = counts.get(e.day_id, 0) + 1
            for day_id, count in counts.items():
                if count > max_per_day:
                    violations.append(MoveViolation(
                        "MAX_PERIODS_PER_DAY",
                        f"Requirement {rid!r} would have {count} periods on {day_id!r}, "
                        f"max_periods_per_day is {max_per_day}",
                    ))

    return violations


def _plans_equivalent(a: MovePlan, b: MovePlan) -> bool:
    """Set-based comparison is safe here, not just convenient: both plans
    being compared were built by ``_build_swap_plan`` from schedules that
    ``validate_move`` has already independently confirmed are HARD-valid,
    and full class occupancy + non-overlap together guarantee at most one
    entry exists per ``(requirement_id, day_id, period_id)``. So neither
    ``removed_entries`` nor ``added_entries`` can ever contain a duplicate
    to lose multiplicity of, and a set match can only occur when the two
    plans genuinely remove/add the exact same entries -- order-invariantly,
    which is correct since application order never matters."""
    return set(a.removed_entries) == set(b.removed_entries) and set(a.added_entries) == set(b.added_entries)


def apply_move(
    problem: SchedulingProblem,
    current_schedule: Schedule,
    result: MoveValidationResult,
    index: ProblemIndex | None = None,
) -> Schedule:
    """Apply a previously validated move to ``current_schedule``.

    ``result`` is never trusted blindly: its ``MoveIntent`` (the original
    requirement/source/target request) is re-validated against
    ``current_schedule`` right here, and only applied if that fresh
    validation is still allowed AND resolves to a plan equivalent to the
    one originally validated. This makes a *stale* plan -- one validated
    against a schedule that has since changed in a way that actually
    affects this move -- impossible to silently apply; it is rejected with
    ``EditingError(code="STALE_MOVE_PLAN")`` instead. An unrelated change
    elsewhere in the schedule that leaves this exact move equally valid is
    deliberately still allowed to proceed.
    """
    if not result.allowed or result.plan is None:
        raise EditingError("Cannot apply a move that was not validated as allowed", code="MOVE_NOT_ALLOWED")

    index = index or ProblemIndex(problem)
    intent = result.intent

    fresh_result = validate_move(
        problem, current_schedule,
        intent.requirement_id, intent.source_day_id, intent.source_period_id,
        intent.target_day_id, intent.target_period_id,
        index=index,
    )
    if not fresh_result.allowed:
        raise EditingError(
            "The previously validated move is no longer valid against the current schedule: "
            + "; ".join(f"{v.code}: {v.message}" for v in fresh_result.violations),
            code="STALE_MOVE_PLAN",
        )
    if not _plans_equivalent(fresh_result.plan, result.plan):
        raise EditingError(
            "The current schedule resolves this move to a different outcome than when it was "
            "originally validated (the underlying occurrences have changed) -- re-validate and "
            "apply the fresh result instead",
            code="STALE_MOVE_PLAN",
        )

    removed_keys = {
        (e.requirement_id, e.day_id, e.period_id, e.source) for e in fresh_result.plan.removed_entries
    }
    remaining = tuple(
        e for e in current_schedule.entries
        if (e.requirement_id, e.day_id, e.period_id, e.source) not in removed_keys
    )
    return current_schedule.with_entries(remaining + fresh_result.plan.added_entries)


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------

def lock_occurrence(
    problem: SchedulingProblem, schedule: Schedule, requirement_id: str, day_id: str, period_id: str,
    index: ProblemIndex | None = None,
) -> Schedule:
    index = index or ProblemIndex(problem)
    occ = find_logical_occurrence(problem, index, schedule, requirement_id, day_id, period_id)
    anchor = occ.period_ids[0]
    new_keys = schedule.locked_occurrences | {OccurrenceKey(rid, day_id, anchor) for rid in occ.requirement_ids}
    return schedule.with_locked(new_keys)


def unlock_occurrence(
    problem: SchedulingProblem, schedule: Schedule, requirement_id: str, day_id: str, period_id: str,
    index: ProblemIndex | None = None,
) -> Schedule:
    index = index or ProblemIndex(problem)
    occ = find_logical_occurrence(problem, index, schedule, requirement_id, day_id, period_id)
    anchor = occ.period_ids[0]
    keys_to_remove = {OccurrenceKey(rid, day_id, anchor) for rid in occ.requirement_ids}
    return schedule.with_locked(schedule.locked_occurrences - keys_to_remove)
