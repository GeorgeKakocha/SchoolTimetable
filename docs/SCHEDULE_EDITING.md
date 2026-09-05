# Schedule Editing (Phase 2C)

This document covers the domain/application behavior added for working
with an *existing* generated timetable: manual moves, locking, and
disruption-minimizing re-optimization. No FastAPI, database, UI, or
persistence is introduced here -- see `docs/PROJECT_STATE.md` for what
remains out of scope.

## New domain types

`domain/schedule.py`:

- **`Schedule`** -- an in-memory snapshot: `entries` (the same
  `ScheduleEntry` tuple a `SchedulingResult` already carries) plus
  `locked_occurrences` (which logical occurrences must not move during
  re-optimization). No database, no version history table -- "the
  previous schedule" is just whatever `Schedule` value the caller passes
  into `reoptimize()`. A dedicated `ScheduleVersion` type was considered
  and not introduced; there was no behavior it would add at this phase.
- **`OccurrenceKey(requirement_id, day_id, anchor_period_id)`** --
  identifies one logical occurrence for locking. See "Logical occurrence"
  below for why the period is required, not optional.

## Logical occurrence

A *logical occurrence* is the atomic unit manual moves and locks operate
on: one requirement's block of periods on one day (possibly just one
period), expanded to include every synchronized split-group sibling's
matching periods on that same day.

Reconstructing "which periods on this day belong together" from an
already-valid schedule is unambiguous **only** for the block policies
whose CP-SAT encoding actually enforces a day-cap:

- **REQUIRED** always has a day-cap (`model_builder._add_required_block_constraints`).
- **PREFERRED with a formed double** has a day-cap only while that
  double genuinely exists that day (`_add_preferred_double_constraints`).
- **FLEXIBLE has no day-cap at all**, and in practice FLEXIBLE
  requirements routinely place several, often non-adjacent, periods on
  the same day (observed directly in the Phase-1 fixture, e.g. one
  requirement occupying all 8 periods of a single Wednesday). Treating
  "same requirement, same day" as one block there would silently glue
  together periods nothing ever required to move together.

Consequence: **a period is always required to identify an occurrence**,
never just a day. For REQUIRED/formed-PREFERRED-double, the day's full
set of periods for that requirement is returned regardless of which
member period was passed in. For everything else, only the one period
given is returned. `OccurrenceKey` stores the occurrence's anchor (lowest
index) period for this reason.

## Split-group move semantics

Since split branches are forced into lock-step by the model
(`x[req_a,d,p] == x[req_b,d,p]` for every single slot, unconditionally), a
schedule that is already valid guarantees every sibling has an entry at
exactly the same periods the primary requirement does on a given day --
this holds regardless of either branch's own block policy, so
reconstructing the full synchronized occurrence (all branches' matching
periods) from any one branch is unambiguous, never a heuristic.
`find_logical_occurrence` expands to every sibling's matching periods and
`lock_occurrence`/`unlock_occurrence` always act on all of them together
-- there is no way to lock only one branch of a split.

## REQUIRED block move semantics

A REQUIRED block (e.g. the "2" in a `(2, 1, 1, 1)` pattern) moves as one
unit: the whole contiguous run, never a subset. `validate_move` computes
the target window as "the same length as the source block, starting at
the given target period, within one structural run" -- if that window
would run off the calendar or cross a break, the move is rejected
(`TARGET_WINDOW_INVALID`) before anything else is checked. Singles in a
REQUIRED pattern (the `1`s) move individually, since each is its own
one-period occurrence.

## Why a manual move is a validated *swap*, not a plain relocation

Full class occupancy is a HARD constraint that is enforced unconditionally,
before and after editing: every instructional slot for every class is
always filled by exactly one activity. A literal "relocate occurrence X
from A to B" would leave A empty -- nothing fills it, by definition of a
single move -- so it can never produce a schedule that still satisfies
full occupancy. Since the brief explicitly requires that an accepted move
produce a schedule passing the same independent verifier as any generated
schedule, a plain relocation can never be an "accepted move" for a fully
packed timetable.

The resolution: **a move is a validated swap**. The moved occurrence and
whatever currently occupies the target window (for the same class(es))
exchange places. This is occupancy-preserving by construction (a
permutation, not a deletion) and gives concrete meaning to
"class occupancy collision" (the target occupant's classes don't match the
mover's) and "full class occupancy invalidated" (there is no clean,
single, whole occurrence to swap with) as real, checkable conditions
rather than conditions that would fire on every single move. This is a
deliberate, explicit design decision resolving a genuine tension in the
brief -- not invented/ambiguous behavior -- and is why `apply_move`
touches two occurrences, not one.

Swap compatibility rules (all enforced in `validate_move`, before
`apply_move` may run):

1. The target window must be occupied by **exactly one** other
   requirement's occurrence (not a reserved block, not zero occupants,
   not periods split across two different occurrences).
2. That occupant's own footprint must exactly match the target window --
   partially overlapping a block is rejected (`REQUIRED_BLOCK_VIOLATION`),
   never silently split.
3. The occupant must cover the **same set of classes** as the mover
   (`CLASS_OCCUPANCY_CONFLICT` otherwise) -- this is what keeps merged
   lessons swap-compatible only with other same-class-set occurrences.
4. Neither side of the swap may be `FixedPlacement`-protected or
   currently locked.
5. After the hypothetical swap, every affected teacher/group/resource/
   `max_periods_per_day` rule is re-checked against the rest of the
   schedule (excluding the two occurrences that are moving).

## Manual-move validation architecture

`scheduling/editing.py` implements **validate first, apply second**,
never "mutate and hope the verifier catches it" -- and, after a
pre-commit audit, two runtime safety guarantees on top of that (see
"Runtime safety guarantees" below):

- `validate_move(problem, schedule, requirement_id, source_day_id,
  source_period_id, target_day_id, target_period_id) ->
  MoveValidationResult` first independently verifies `schedule` is itself
  HARD-valid, then computes everything else (the swap plan, every
  HARD-rule check) without touching the `Schedule`. On success it returns
  `allowed=True` plus a `MovePlan` (the exact entries to remove/add) and
  the original `MoveIntent`; on failure, `allowed=False` plus one or more
  `MoveViolation(code, message)` (and no `plan`).
- `apply_move(problem, current_schedule, result) -> Schedule` requires
  `result.allowed`, then **re-runs `validate_move` against
  `current_schedule`** using `result.intent` (never `result.plan`
  directly) and only applies the fresh plan if that revalidation is still
  allowed and resolves to a plan equivalent to the one originally
  validated -- see "Runtime safety guarantees" below.

### Validation codes

| Code | Meaning |
|---|---|
| `INVALID_SCHEDULE` | The input `Schedule` handed to `validate_move` (or resolved by `find_logical_occurrence`) is not itself HARD-valid; no move can be safely reasoned about. |
| `OCCURRENCE_NOT_FOUND` | The source (requirement, day, period) doesn't resolve to a real occurrence. |
| `FIXED_PLACEMENT` | Either side of the swap is protected by a configured `FixedPlacement`. |
| `LOCKED_OCCURRENCE` | Either side of the swap is currently locked. |
| `TARGET_WINDOW_INVALID` | The target window would cross a structural break or run past the calendar. |
| `RESERVED_BLOCK_CONFLICT` | The target window is occupied by a reserved/club block. |
| `TARGET_OCCUPANT_INCOMPATIBLE` | The target window isn't occupied by exactly one swappable occurrence. |
| `REQUIRED_BLOCK_VIOLATION` | The target window only partially overlaps an existing block. |
| `CLASS_OCCUPANCY_CONFLICT` | The target occupant covers a different set of classes than the mover. |
| `TEACHER_CONFLICT` | A teacher would be double-booked after the swap. |
| `TEACHER_UNAVAILABLE` | A teacher is UNAVAILABLE at (part of) the new slot. |
| `GROUP_CONFLICT` | A participant group would be double-booked after the swap. |
| `RESOURCE_CAPACITY` | A resource's capacity would be exceeded after the swap. |
| `MAX_PERIODS_PER_DAY` | A requirement would exceed its configured daily cap after the swap. |

`apply_move` can additionally raise `EditingError` with:

| Code | Meaning |
|---|---|
| `MOVE_NOT_ALLOWED` | Called with a `MoveValidationResult` that was never `allowed`. |
| `STALE_MOVE_PLAN` | The original move is no longer valid, or now resolves differently, against the schedule `apply_move` was actually given -- see below. |

## Runtime safety guarantees (added after a pre-commit audit)

Two blockers were found reviewing Phase 2C before commit, both about
trusting externally-supplied state that had no business being trusted
blindly. Both are now closed:

### 1. The input `Schedule` is never trusted blindly

`validate_move` re-verifies (via the unmodified independent
`verification.verifier.verify`, never a duplicated rule set) that its
`schedule` argument is HARD-valid *before* reasoning about a move at all;
if not, it rejects with `INVALID_SCHEDULE` and produces no `MovePlan`.
`find_logical_occurrence` -- a public helper `lock_occurrence`,
`unlock_occurrence`, and `reoptimize` also call directly -- additionally
verifies on its own that a REQUIRED block (or a formed PREFERRED double)
it is about to treat as one occurrence is genuinely a single consecutive
same-`block_id` run, raising `EditingError(code="INVALID_SCHEDULE")`
rather than silently grouping a malformed schedule (e.g. non-adjacent
periods like `q1`/`q3` crossing a structural break) as if it were valid.

### 2. A validated plan can never be blindly applied to a schedule that has since changed

Every `MoveValidationResult` carries the original `MoveIntent`
(`requirement_id`, `source_day_id`, `source_period_id`, `target_day_id`,
`target_period_id`) -- the request, not just its computed outcome.
`apply_move(problem, current_schedule, result)`:

1. Re-runs `validate_move` against `current_schedule` using `result.intent`.
2. Requires that fresh validation to still be `allowed`.
3. Requires the fresh `MovePlan` to be semantically equivalent (same
   removed/added entries, as sets) to the one originally validated.
4. Only then applies the *fresh* plan -- never the possibly-stale one
   captured on `result`.

If either check fails, `apply_move` raises
`EditingError(code="STALE_MOVE_PLAN")` and `current_schedule` is
untouched. This deliberately still allows an *unrelated* schedule change
that leaves the exact same logical move valid to proceed -- only a change
that actually invalidates or redefines this move is rejected. Future API
callers may treat validate/apply as an optimistic two-step workflow
(validate once, let a user review, apply later), but `apply_move` is the
authoritative check; no persistence or schedule-versioning layer is
introduced by this guarantee, and none is needed for it to work.

## Locking

A lock means "this occurrence must remain in this exact placement during
re-optimization." Lock state lives on `Schedule`
(`locked_occurrences: frozenset[OccurrenceKey]`), not on
`TeachingRequirement` -- the same requirement's Monday occurrence can be
locked while its Wednesday occurrence is not.

- `lock_occurrence(problem, schedule, requirement_id, day_id, period_id)`
  and `unlock_occurrence(...)` resolve the full logical occurrence first
  (expanding to every split sibling) and lock/unlock **all** of its
  member requirement IDs together -- there is no way to end up with only
  one branch of a split locked, or only part of a REQUIRED block locked.
- A manual move does **not** imply a lock; locking is always a separate,
  explicit call. `validate_move` additionally refuses to move (or
  displace) a currently-locked occurrence (`LOCKED_OCCURRENCE`).

### `FixedPlacement` vs. manual lock

These are deliberately independent mechanisms:

| | `FixedPlacement` | manual lock |
|---|---|---|
| Where it lives | `SchedulingProblem.fixed_placements` (school-configured input) | `Schedule.locked_occurrences` (session-local editing state) |
| Scope | Always HARD, in every solve/re-optimize, unconditionally | HARD only during `reoptimize()`, and only for the `Schedule` it's attached to |
| Manual move | Always forbidden to move | Forbidden to move while locked; unlockable |
| Set by | The school's declared configuration | A human, during an editing session |

A `FixedPlacement` is never affected by lock/unlock calls, and locking an
occurrence never creates or removes a `FixedPlacement`. `reoptimize()`
enforces both independently and simultaneously -- see `_add_fixed_placements`
(reused unchanged from `model_builder`) and `_add_lock_constraints`.

## Re-optimization architecture

`reoptimize()` also never trusts its `reference_schedule` argument
blindly -- but a reference schedule needs a fundamentally different kind
of sanity check than the `problem` it's re-optimized against, and
conflating the two was a real bug caught in review before this shipped.
Two distinct questions, both worth asking, are NOT the same question:

1. **Is the reference schedule STRUCTURALLY COHERENT** -- a genuine,
   internally consistent timetable, safe to decompose into logical
   occurrences and disruption-tracking groups?
2. **Is the reference schedule FEASIBLE under the *current* `problem`?**

`reoptimize` only ever asks question 1 of its `reference_schedule`
argument, via `_reference_schedule_structural_violations` -- reusing the
relevant `verification.verifier` check functions directly (the same
pattern as reusing `model_builder`'s constraint functions below), never
duplicating their logic:

| Retained (structure/topology -- must always hold) | Excluded (current-problem placement feasibility -- may have legitimately changed) |
|---|---|
| No duplicate/double-booked teacher or participant-group entries | Teacher-availability (`UNAVAILABLE`) compliance |
| Each requirement's entry count matches its declared weekly count | `FixedPlacement` compliance |
| REQUIRED blocks are genuine consecutive same-`block_id` runs | Resource capacity |
| Split-group siblings are synchronized | `ReservedBlock` compliance |
| Merged-group entries record the correct class set | `max_periods_per_day` |
| Full class occupancy holds | |

Question 2 is deliberately **never** asked of the reference up front --
it is exactly the question `reoptimize`'s two-phase CP-SAT solve exists
to answer and repair. A reference schedule is *expected* to fail question
2 the moment it's handed to `reoptimize`: that a teacher just became
`UNAVAILABLE` where they used to teach, a resource's capacity just
tightened, a `ReservedBlock`/`FixedPlacement` just moved onto an old
lesson's slot, or `max_periods_per_day` just tightened, is the *normal,
expected trigger* for calling `reoptimize` at all (see
`run_editing_demo.py` step 5 and `test_reoptimize.py` scenarios B and
E-H) -- never evidence of a corrupted `Schedule` object. Rejecting the
reference on that basis, rather than trying to repair it, would make the
re-optimization workflow this module exists for impossible. The rebuilt
CP-SAT model (`_build_hard_model`) enforces every one of the *current*
problem's placement-feasibility rules unconditionally regardless, so
nothing about final-result HARD correctness is weakened by not
pre-checking them against the reference.

If any structural check fails, `reoptimize` returns
`SolverStatus.INVALID_INPUT` with a `ValidationError` immediately, never
falls back to an unlocked solve, and never mutates the reference
schedule.

`scheduling/reoptimize.py::reoptimize(problem, reference_schedule, options)`
reuses the exact same HARD-constraint-adding functions from
`model_builder.py` (teacher/group non-overlap, full class occupancy,
resource capacity, REQUIRED blocks, split sync, merged coverage, reserved
blocks, fixed placements, `max_periods_per_day`) -- nothing about HARD
feasibility is re-implemented or weakened. On top of that unchanged
foundation it adds:

1. **Lock constraints**: for every `(requirement_id, day_id, period_id)`
   in the reference schedule whose logical occurrence is locked,
   `model.Add(lesson_var == 1)`.
2. **Disruption tracking**: the reference schedule is decomposed into the
   same logical occurrences `validate_move`/locking use (split siblings
   collapse to one unit; a REQUIRED/formed-PREFERRED-double block is one
   unit; FLEXIBLE periods that merely share a day stay separate units).
   For each, a boolean `preserved` is true iff *every* member period
   keeps its exact `(day, period)` in the new solution; disruption is
   `sum(1 - preserved)` over all occurrences.

### Objective priority: two-phase lexicographic solving

The brief requires disruption to strictly outrank the ordinary Phase-1/2A
soft preferences, not be folded into one arbitrary weighted sum. CP-SAT
supports genuine lexicographic priority cleanly enough that no numeric
weight compromise was needed:

1. **Phase 1**: build the model (HARD constraints + locks + disruption
   terms), minimize disruption alone, solve.
2. If phase 1 is not proven `OPTIMAL` (time-limited), its best-effort
   result is returned as-is -- pinning a non-proven disruption value and
   re-solving on top of it could make phase 2 wrongly infeasible or
   suboptimal, so phase 2 is skipped rather than risk that.
3. **Phase 2** (only when phase 1 is `OPTIMAL`): rebuild the model fresh,
   add `disruption_expr == D*` (the proven-optimal value from phase 1),
   and minimize the ordinary soft-preference objective (teacher
   `PREFER_NOT`, preferred periods, preferred double, `min_distinct_days`)
   among the solutions that already achieve minimal disruption.

This is two separate `CpSolver` invocations, not one model with mixed
weights -- disruption can never be outweighed by, or need to be
numerically balanced against, the ordinary soft preferences.

## Disruption metric

Simple and explainable, exactly as specified: for each logical occurrence
in the reference schedule, `0` if it keeps every member's exact slot,
`1` if it moves at all (regardless of how far). No distance weighting.
`SchedulingResult.metadata` reports `num_moved_occurrences`,
`num_preserved_occurrences`, and `disruption_penalty` (equal to
`num_moved_occurrences`) whenever a result was produced.

## Failure behavior

- `reoptimize()` on an infeasible combination (e.g. a lock directly
  contradicting a new HARD condition, such as locking an occurrence into
  a slot a subsequent availability change makes UNAVAILABLE) returns
  `INFEASIBLE` from CP-SAT -- locks are never silently dropped to
  manufacture a feasible-looking result.
- `validate_move()` never mutates anything on rejection; `apply_move()`
  raises if called on a non-`allowed` result, an invalid input schedule
  (`INVALID_SCHEDULE`), or a plan that is no longer valid or resolves
  differently against the schedule it is actually given
  (`STALE_MOVE_PLAN`) -- rather than guessing or silently substituting a
  materially different move.
- Every schedule produced by a move, a lock, or a re-optimization is
  expected to pass the unmodified independent verifier
  (`verification/verifier.py`) -- and does, per the Phase 2C test suite;
  no verifier rule was relaxed for editing.
