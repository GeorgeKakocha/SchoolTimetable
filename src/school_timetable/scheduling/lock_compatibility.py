"""Safe Configuration Changes, Slice C, Checkpoint 1: pure, in-memory
classification of whether a historical `ScheduleVersion`'s
`LockedOccurrence`s remain valid ("compatible") against a *different*
(draft) `SchedulingProblem` -- Owner Decision #39 item (4)'s "identify
affected locks" requirement, made deterministic and solver-free.

No persistence, no `Session`, no API dependency: takes plain domain
objects only and returns a plain result. The real CP-SAT solve (a later
Slice C checkpoint) is responsible for pinning `compatible_keys` as HARD
constraints and for proving their JOINT feasibility together with every
other draft constraint -- this module only proves what can be proven
deterministically from individual locks and REQUIRED block multiplicities.
It deliberately never attempts
to prove that several compatible locks can all hold simultaneously; that
is the solver's job (an `INFEASIBLE` result there is the correct, already-
modeled outcome for a joint conflict this module cannot see).

Two independent things must both hold for a historical lock to be
"compatible":

1. STRUCTURAL resolution: the lock's own `(requirement_id, day_id,
   anchor_period_id)` triple, and every split-sibling it implies, must
   still resolve to a genuine logical occurrence under the draft
   `SchedulingProblem` -- reusing `scheduling.editing.find_logical_occurrence`
   exactly as `scheduling.reoptimize` already does for its own lock
   handling, never a second, parallel reimplementation of that
   resolution logic. A `find_logical_occurrence` failure is `EditingError`
   with a `code` of either `OCCURRENCE_NOT_FOUND` (unknown requirement, or
   no historical entry at all for this exact day/period) or
   `INVALID_SCHEDULE` (a split-sibling desync, or a REQUIRED/formed-
   PREFERRED-double block whose historical periods no longer form one
   consecutive same-`block_id` run under the draft's period layout).
   Some transitively-required references (a locked requirement's own
   `Period`, or its `ParticipantGroup`) are not separately validated by
   `find_logical_occurrence` itself and can surface as a plain `KeyError`
   instead -- caught here defensively as a third, more generic
   structural reason, never allowed to propagate as an unexpected
   exception.

2. POINTWISE hard-constraint validity: pinning that exact occurrence (and
   its already-resolved sibling members) to its historical placement must
   not, by itself, violate one of a small set of `verification.verifier`
   hard-constraint checks that are genuinely safe to evaluate against one
   synthesized occurrence in isolation -- teacher availability at that
   exact slot, and the resource capacity of the resource its own
   requirement declares -- reusing those checks' exact implementations
   directly (the same underscore-prefixed-function import pattern
   `scheduling.reoptimize` already uses for several verifier checks),
   rather than re-deriving equivalent logic. Checks that only make sense
   evaluated against a COMPLETE schedule (full class occupancy, weekly
   lesson-count totals, teacher/participant-group non-overlap against
   OTHER, unrelated entries) are deliberately never invoked here --
   reusing them on one isolated occurrence would either be meaningless or
   would require solving jointly, which belongs to the real solver.

   REQUIRED patterns are checked against retained LOCKED blocks only, as a
   size multiset subset of the draft pattern. Unlocked historical days do
   not consume pattern capacity. Stable natural-key order retains the first
   compatible blocks and attributes multiplicity overflow to excess keys.
   Synchronized sibling keys referring to the same block count only once
   per requirement; a rejected key never consumes pattern capacity.

   Fixed placements and this lock must fit the requirement's weekly
   period budget and, for REQUIRED patterns, its number of distinct block
   days. Different slots alone are not a conflict: a fixed placement pins
   one lesson, not the entire requirement. Other joint feasibility stays
   with the solver.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.scheduling.editing import EditingError, LogicalOccurrence, find_logical_occurrence
from school_timetable.verification.verifier import (
    _check_resource_capacity,
    _check_teacher_unavailable_respected,
)

# Stable, API-safe reason codes -- never a persistence surrogate ID, never
# raw exception text as the *code* (only as the accompanying message).
REQUIREMENT_OR_SLOT_DELETED = "REQUIREMENT_OR_SLOT_DELETED"
OCCURRENCE_STRUCTURE_INVALID = "OCCURRENCE_STRUCTURE_INVALID"
OCCURRENCE_UNRESOLVABLE = "OCCURRENCE_UNRESOLVABLE"
TEACHER_UNAVAILABLE_AT_SLOT = "TEACHER_UNAVAILABLE_AT_SLOT"
FIXED_PLACEMENT_CONFLICT = "FIXED_PLACEMENT_CONFLICT"
REQUIRED_BLOCK_PATTERN_INVALID = "REQUIRED_BLOCK_PATTERN_INVALID"
RESOURCE_DELETED = "RESOURCE_DELETED"
RESOURCE_CAPACITY_EXCEEDED = "RESOURCE_CAPACITY_EXCEEDED"


@dataclass(frozen=True)
class IncompatibleLock:
    """One historical lock that cannot be carried forward into the draft
    configuration unchanged. `key` preserves the exact natural-ID identity
    (`requirement_id`/`day_id`/`anchor_period_id`) the caller supplied --
    never a persistence surrogate ID. `reason_code` is one of this
    module's stable constants; `message` is a human-readable detail safe
    to surface to an API caller (natural IDs only, no exception `repr`)."""

    key: OccurrenceKey
    reason_code: str
    message: str


@dataclass(frozen=True)
class LockClassification:
    """The result of classifying one active version's entire
    `locked_occurrences` set against a draft `SchedulingProblem`.
    `compatible_keys` are exactly the input keys found valid in isolation
    -- callers pin these as HARD constraints in the real solve.
    `incompatible` names every other input key, each with its own reason.
    Every input key appears in exactly one of the two -- never both, never
    neither."""

    compatible_keys: frozenset[OccurrenceKey]
    incompatible: tuple[IncompatibleLock, ...]


def classify_locks(
    problem: SchedulingProblem,
    index: ProblemIndex,
    reference_entries: tuple[ScheduleEntry, ...],
    locked_occurrences: frozenset[OccurrenceKey],
) -> LockClassification:
    """Classify every key in `locked_occurrences` (an active
    `ScheduleVersion`'s locks) against `problem`/`index` (the DRAFT
    configuration being regenerated against) using `reference_entries`
    (that same active version's complete historical entries -- needed to
    reconstruct which periods/siblings each locked occurrence actually
    spans). Only resolved locked blocks consume REQUIRED pattern capacity;
    unlocked historical occurrences need not match the new pattern.

    Pure and read-only: never mutates `problem`/`index`/`reference_entries`,
    never touches a database or the solver. Iterates in a fixed, sorted
    order so `incompatible`'s order is deterministic for identical input."""
    if not locked_occurrences:
        return LockClassification(compatible_keys=frozenset(), incompatible=())

    reference_schedule = Schedule(entries=reference_entries)

    compatible: set[OccurrenceKey] = set()
    incompatible: list[IncompatibleLock] = []
    retained_blocks: dict[str, set[tuple[str, tuple[str, ...]]]] = {}

    for key in sorted(locked_occurrences, key=lambda k: (k.requirement_id, k.day_id, k.anchor_period_id)):
        result = _classify_one(problem, index, reference_schedule, key, retained_blocks)
        if result is None:
            compatible.add(key)
        else:
            incompatible.append(result)

    return LockClassification(compatible_keys=frozenset(compatible), incompatible=tuple(incompatible))


def resolve_compatible_members(
    problem: SchedulingProblem,
    index: ProblemIndex,
    reference_entries: tuple[ScheduleEntry, ...],
    compatible_keys: frozenset[OccurrenceKey],
) -> frozenset[tuple[str, str, str]]:
    """Safe Configuration Changes, Slice C, Checkpoint 4:
    `GenerateScheduleService.regenerate()`'s bridge from `classify_locks`'
    already-proven-compatible `OccurrenceKey`s to the exact CP-SAT lesson
    variable keys (`(requirement_id, day_id, period_id)`, `model_builder
    .LessonKey`'s own shape) a fresh solve must pin as HARD constraints --
    it is never enough to persist a compatible lock's metadata unchanged;
    the actual solved schedule must contain it. Every `key` here is
    resolved via `find_logical_occurrence` exactly as `_classify_one`
    already does for its own compatibility check -- never a second,
    parallel occurrence-resolution algorithm -- and is guaranteed to
    resolve without raising, since `classify_locks` already proved
    exactly that for every key in `compatible_keys`.

    A single-period occurrence resolves to just itself; a REQUIRED block
    or a split-group/formed-PREFERRED-double occurrence resolves to
    every one of its sibling members, all of which must be pinned
    together for the lock to mean what it claims."""
    reference_schedule = Schedule(entries=reference_entries)
    members: set[tuple[str, str, str]] = set()
    for key in compatible_keys:
        occurrence = find_logical_occurrence(
            problem, index, reference_schedule, key.requirement_id, key.day_id, key.anchor_period_id,
        )
        members.update((m.requirement_id, m.day_id, m.period_id) for m in occurrence.members)
    return frozenset(members)


def _classify_one(
    problem: SchedulingProblem,
    index: ProblemIndex,
    reference_schedule: Schedule,
    key: OccurrenceKey,
    retained_blocks: dict[str, set[tuple[str, tuple[str, ...]]]],
) -> IncompatibleLock | None:
    try:
        occurrence = find_logical_occurrence(
            problem, index, reference_schedule, key.requirement_id, key.day_id, key.anchor_period_id,
        )
        req_by_id = {rid: index.requirements_by_id[rid] for rid in occurrence.requirement_ids}
        synthetic_entries = _synthesize_entries(occurrence, req_by_id)
    except EditingError as exc:
        reason_code = OCCURRENCE_STRUCTURE_INVALID if exc.code == "INVALID_SCHEDULE" else REQUIREMENT_OR_SLOT_DELETED
        return IncompatibleLock(key=key, reason_code=reason_code, message=str(exc))
    except KeyError as exc:
        # Defense-in-depth: a locked requirement's own Period or
        # ParticipantGroup no longer resolving in the draft is not
        # expected to be reachable through any legitimate write path
        # (deleting a referenced entity is itself blocked elsewhere), but
        # this classifier must never let an unexpected exception
        # propagate as if it were an internal defect -- it is reported as
        # a structural incompatibility instead, matching this codebase's
        # existing defense-in-depth convention.
        return IncompatibleLock(
            key=key, reason_code=OCCURRENCE_UNRESOLVABLE,
            message=(
                f"occurrence for requirement={key.requirement_id!r}, day={key.day_id!r}, "
                f"anchor_period={key.anchor_period_id!r} could not be resolved against the draft "
                f"configuration: {exc!r}"
            ),
        )

    teacher_violations = _check_teacher_unavailable_respected(index, synthetic_entries)
    if teacher_violations:
        return IncompatibleLock(key=key, reason_code=TEACHER_UNAVAILABLE_AT_SLOT, message=teacher_violations[0])

    fixed_placement_message = _fixed_placement_conflict(problem, occurrence, req_by_id)
    if fixed_placement_message is not None:
        return IncompatibleLock(key=key, reason_code=FIXED_PLACEMENT_CONFLICT, message=fixed_placement_message)

    required_block_message = _required_block_pattern_violation(occurrence, req_by_id, retained_blocks)
    if required_block_message is not None:
        return IncompatibleLock(
            key=key, reason_code=REQUIRED_BLOCK_PATTERN_INVALID, message=required_block_message,
        )

    resource_result = _resource_violation(index, occurrence, req_by_id)
    if resource_result is not None:
        reason_code, message = resource_result
        return IncompatibleLock(key=key, reason_code=reason_code, message=message)

    # Reserve only after every direct check passed, and only once per
    # requirement/block even when several split-sibling keys name it.
    for requirement_id in occurrence.requirement_ids:
        if req_by_id[requirement_id].block_policy.mode == BlockPolicyMode.REQUIRED:
            retained_blocks.setdefault(requirement_id, set()).add((occurrence.day_id, occurrence.period_ids))
    return None


def _synthesize_entries(
    occurrence: LogicalOccurrence, req_by_id: dict[str, TeachingRequirement],
) -> tuple[ScheduleEntry, ...]:
    """Builds the minimal `ScheduleEntry` tuple representing exactly this
    occurrence's placement in the DRAFT's own terms (its requirement(s)'
    current teacher/participant-group/resource) -- never the historical
    entries' own stored fields, which describe the OLD revision. Used only
    to feed the reused pointwise verifier checks below; never persisted,
    never returned to any caller."""
    # class_sections is left empty: none of the reused pointwise checks
    # below (_check_teacher_unavailable_respected, _check_resource_capacity)
    # inspects it -- populating it correctly would need the draft's
    # ParticipantGroup resolved via `index`, purely for an unused field.
    entries = []
    for member in occurrence.members:
        req = req_by_id[member.requirement_id]
        resource_id = req.resource_requirement.resource_id if req.resource_requirement else None
        entries.append(ScheduleEntry(
            source=EntrySource.REQUIREMENT,
            activity_id=req.activity_id,
            day_id=member.day_id,
            period_id=member.period_id,
            class_sections=(),
            teacher_id=req.teacher_id,
            participant_group_id=req.participant_group_id,
            resource_id=resource_id,
            requirement_id=req.id,
        ))
    return tuple(entries)


def _fixed_placement_conflict(
    problem: SchedulingProblem,
    occurrence: LogicalOccurrence,
    req_by_id: dict[str, TeachingRequirement],
) -> str | None:
    """Prove only local cardinality conflicts with this occurrence's pins.

    A fixed placement names a lesson-period, not an occurrence anchor. Use
    all resolved block/split members and union slots so an already-fixed
    member (or duplicate fixed row) never consumes an extra lesson. REQUIRED
    patterns allow exactly one block per occupied day: distinct days give a
    lower bound on the blocks needed, not one block per fixed member period.
    FLEXIBLE/PREFERRED have no hard occurrence count from block_sizes.
    Passing these necessary checks is not a feasibility proof; assigning
    block lengths/windows and satisfying other constraints belongs to solve.
    """
    for requirement_id in occurrence.requirement_ids:
        fixed_slots = {
            (fp.slot.day_id, fp.slot.period_id)
            for fp in problem.fixed_placements if fp.requirement_id == requirement_id
        }
        if not fixed_slots:
            continue
        required_slots = fixed_slots | {
            (member.day_id, member.period_id)
            for member in occurrence.members if member.requirement_id == requirement_id
        }
        req = req_by_id[requirement_id]
        if len(required_slots) > req.weekly_periods:
            return (
                f"Fixed placements and this lock require {len(required_slots)} distinct lesson slots "
                f"for requirement {requirement_id!r}, exceeding its {req.weekly_periods} weekly periods"
            )
        if req.block_policy.mode == BlockPolicyMode.REQUIRED:
            required_days = {day_id for day_id, _ in required_slots}
            block_count = len(req.block_policy.block_sizes)
            if len(required_days) > block_count:
                return (
                    f"Fixed placements and this lock require {len(required_days)} distinct days "
                    f"for requirement {requirement_id!r}, exceeding its {block_count} REQUIRED blocks"
                )
    return None


def _required_block_pattern_violation(
    occurrence: LogicalOccurrence,
    req_by_id: dict[str, TeachingRequirement],
    retained_blocks: dict[str, set[tuple[str, tuple[str, ...]]]],
) -> str | None:
    """Check locked-block multiplicity, never the entire historical schedule.

    Resolution already proved continuity and split synchronization. Each
    REQUIRED pattern element is one whole block on a distinct day. The
    caller visits keys in stable natural-ID order; previously accepted
    blocks consume capacity, while duplicate references to one block do not.
    This check does not reserve capacity until all other checks also pass.
    """
    block = (occurrence.day_id, occurrence.period_ids)
    size = occurrence.length
    for requirement_id in occurrence.requirement_ids:
        policy = req_by_id[requirement_id].block_policy
        if policy.mode != BlockPolicyMode.REQUIRED:
            continue
        retained = retained_blocks.get(requirement_id, set())
        if block in retained:
            continue
        allowed = policy.block_sizes.count(size)
        used = sum(len(period_ids) == size for _, period_ids in retained)
        if used >= allowed:
            return (
                f"Locked occurrence of size {size} for requirement {requirement_id!r} "
                f"cannot be accommodated by REQUIRED block pattern {sorted(policy.block_sizes)!r}: "
                f"{used} size-{size} block(s) already retained in natural-key order, "
                f"{allowed} allowed"
            )
    return None


def _resource_violation(
    index: ProblemIndex, occurrence: LogicalOccurrence, req_by_id: dict[str, TeachingRequirement],
) -> tuple[str, str] | None:
    """A single locked occurrence's own resource usage is either 1 (its
    own members never coincide with each other on the same resource/day/
    period -- they are different periods by construction) or the resource
    the requirement declares no longer exists at all in the draft. Genuine
    capacity CONTENTION with other, unrelated activities can only be
    proven by the real solve (Requirement 9) -- this only catches a
    resource that could never fit even this one lock alone (capacity < 1)
    or that has been deleted outright, both provable deterministically."""
    for member in occurrence.members:
        req = req_by_id[member.requirement_id]
        if req.resource_requirement is None:
            continue
        resource_id = req.resource_requirement.resource_id
        if resource_id not in index.resources_by_id:
            return (
                RESOURCE_DELETED,
                f"Resource {resource_id!r} required by {member.requirement_id!r} no longer exists "
                "in the draft configuration",
            )
        single_use_entry = (ScheduleEntry(
            source=EntrySource.REQUIREMENT,
            activity_id=req.activity_id,
            day_id=member.day_id,
            period_id=member.period_id,
            class_sections=(),
            resource_id=resource_id,
            requirement_id=req.id,
        ),)
        violations = _check_resource_capacity(index, single_use_entry)
        if violations:
            return (RESOURCE_CAPACITY_EXCEEDED, violations[0])
    return None
