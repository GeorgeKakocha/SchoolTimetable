# Solver Contract

## Input

A `SchedulingProblem` (see `src/school_timetable/domain/problem.py`),
containing: school/academic year identity, days, periods, teachers,
teacher availabilities, class sections, participant groups, activities,
teaching requirements, resources, reserved blocks, and fixed placements.
Nothing is hard-coded: day/period counts, teacher counts, subject counts,
etc. are all read from this object.

`solve(problem, options: SolverOptions | None = None)` also accepts an
optional `SolverOptions` (Phase 2B; `scheduling/options.py`) controlling
`max_time_seconds` (default 30.0), `num_search_workers` (default 8), and
`random_seed` (default `None`, i.e. OR-Tools' own default). It is a plain
dataclass with no OR-Tools types in it. Omitting it (`solve(problem)`)
reproduces the exact Phase 1/2A default behavior.

## Output

A `SchedulingResult`:

- `status: SolverStatus` -- one of `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`,
  `INVALID_INPUT`, `ERROR`. A realistic problem may legitimately return
  `FEASIBLE` (a valid schedule found, optimality not proven within the
  time budget) -- this is a successful result, not a failure, as long as
  the independent verifier passes.
- `entries: tuple[ScheduleEntry, ...]` -- populated only for
  `OPTIMAL`/`FEASIBLE`. Each entry carries activity, teacher (if any),
  participant group (if any), class sections occupied, day, period, and
  resource (if any).
- `total_soft_penalty: int` -- the objective value (0 if no soft terms
  applied).
- `validation_errors: tuple[ValidationError, ...]` -- populated only for
  `INVALID_INPUT`.
- `metadata: dict` -- benchmark/observability fields (Phase 2B). Contents
  depend on how far `solve()` got, not on the public `status` value alone:
  - `INVALID_INPUT` -- preflight rejected the input before a CP-SAT model
    was ever built. `metadata` is empty; there is no solver run to report on.
  - A model-build exception (a solver-implementation bug, not bad input)
    -- CP-SAT never ran. `metadata` contains only `{"error": ...}`.
  - Every other case -- CP-SAT actually ran to completion, including a
    raw status such as `UNKNOWN` that the public `SolverStatus` maps to
    `ERROR` -- `metadata` carries the full normal benchmark fields below,
    because the solver genuinely executed and those statistics are real:
    `wall_time_seconds`, `raw_status` (CP-SAT's own status name),
    `num_conflicts`, `num_branches`, `num_lesson_variables` (the
    `x[requirement, day, period]` decision variables), `num_cp_variables`
    and `num_cp_constraints` (total CP-SAT model size, including
    block/window/day-used auxiliary variables), and -- only when the
    model has an objective -- `best_objective_bound` and, for successful
    (`OPTIMAL`/`FEASIBLE`) results, `objective_value`.

  This is not a general telemetry system; it is exactly the fields needed
  to reason about solver scaling (see `docs/SCALE_VALIDATION.md`). Public
  `SolverStatus` semantics are unchanged -- `ERROR` still covers both the
  model-build-exception case and the unmapped-raw-status case; only the
  `metadata` contract distinguishes them.

## HARD constraints (must always hold)

1. Teacher non-overlap.
2. Participant-group non-overlap.
3. Full class occupancy: every main class has exactly one activity in
   every instructional period (pilot-fixture requirement).
4. Every `TeachingRequirement` receives exactly its `weekly_periods`.
5. Teacher `UNAVAILABLE` slots are never used.
6. Reserved/club blocks: ordinary lessons are forbidden in a class's
   reserved slots; the reserved activity occupies them instead.
7. `REQUIRED` lesson-block patterns (generalized in Phase 2A):
   `LessonBlockPolicy.block_sizes` is an arbitrary multiset of positive
   block lengths that must sum to `weekly_periods` -- e.g. `(2, 1, 1, 1)`,
   `(2, 2)`, `(3, 1)`. Every element is placed on its own distinct day; a
   block of length > 1 occupies a genuinely consecutive run of
   instructional periods and can never straddle a structural break (e.g.
   lunch) -- enforced via `Period.block_id` and `domain.calendar.period_windows`.
   This is enforced exactly by CP-SAT, not merely encouraged.
   `PREFERRED` intentionally keeps the original, narrower Phase-1 shape
   (at most one size-2 block, the rest singles) -- see `DECISIONS.md`.
8. Parallel split groups: branches sharing a `split_group_id` are always
   scheduled in identical slots, with the parent class(es) occupied once.
9. Merged groups occupy every underlying class simultaneously.
10. Resource capacity is never exceeded in any slot.
11. Fixed placements are always honored.
12. `max_periods_per_day`, when set on a requirement, is respected.

## SOFT constraints (minimized, never silently violate a HARD one)

1. Teacher `PREFER_NOT` slot usage.
2. Non-preferred period usage (`TimePreference`).
3. A `PREFERRED` double lesson not being formed (Phase-1 shape only --
   at most one size-2 block; not generalized to arbitrary patterns).
4. `min_distinct_days` shortfall.

Weights are centralized in `scheduling/weights.py` as three tiers
(`LOW`/`MEDIUM`/`HIGH`) mapped to integers -- no magic numbers scattered
through solver code.

## Preflight validation

`validation/preflight.py` runs before CP-SAT and rejects (as
`INVALID_INPUT`) obviously broken input: unknown references (teacher,
activity, participant group, class, resource, requirement, slot),
malformed block patterns, a fixed placement landing on a teacher's
`UNAVAILABLE` slot, a teacher required for more weekly periods than they
have usable slots, inconsistent split-group branches, and a class
occupancy total that cannot possibly reach full occupancy. It never looks
at CP-SAT.

REQUIRED-pattern-specific checks (Phase 2A): `TOO_MANY_BLOCKS_FOR_AVAILABLE_DAYS`
(more pattern elements than configured school days), `BLOCK_EXCEEDS_MAX_PERIODS_PER_DAY`
(a block longer than a configured `max_periods_per_day`), and
`BLOCK_LENGTH_UNPLACEABLE` (a block length longer than every consecutive
same-`block_id` run in the calendar). PREFERRED patterns still use the
narrower Phase-1 `UNSUPPORTED_BLOCK_SIZE` check instead. `NON_POSITIVE_BLOCK_LENGTH`
and `BLOCK_PATTERN_TOTAL_MISMATCH` apply to both modes.

## Independent verification

`verification/verifier.py` re-derives every hard constraint directly from
the final `ScheduleEntry` list and the plain domain model -- it does not
read CP-SAT variables and does not assume the solver's constraints were
encoded correctly. If a solver bug exists, this is what catches it. This
is unchanged and applies equally to schedules produced by `solve()`, by a
manual move, or by `reoptimize()` -- see `docs/SCHEDULE_EDITING.md`.

## Re-optimization (Phase 2C)

`scheduling/reoptimize.py::reoptimize(problem, reference_schedule, options=None)`
is a second solver entry point, alongside `solve()`, for working with an
*existing* schedule (`domain.schedule.Schedule`) rather than generating a
fresh one:

- Runs the same `run_preflight` gate as `solve()` -- `INVALID_INPUT` on
  failure, before any model is built.
- Additionally, independently checks `reference_schedule` has valid
  STRUCTURE/TOPOLOGY (no double-booked entries, correct weekly counts,
  well-formed REQUIRED blocks, synchronized splits, consistent
  merged-group classes, full class occupancy) before building any lock
  constraints or disruption groups from it -- `INVALID_INPUT` on failure,
  never an unlocked fallback solve, and the reference is never mutated.
  This is a distinct question from "is the reference *feasible* under the
  current `problem`", which is deliberately never asked up front:
  teacher-availability, `FixedPlacement`, resource-capacity, `ReservedBlock`,
  and `max_periods_per_day` compliance against `problem` are excluded from
  this check, since the reference violating exactly one of those (having
  been valid when generated, before `problem` changed) is the normal
  trigger for calling `reoptimize` in the first place -- the whole point
  is to repair it, not reject it. See `docs/SCHEDULE_EDITING.md`.
- Enforces every HARD constraint `solve()` does, reused unchanged from
  `model_builder`, plus one more: every logical occurrence in
  `reference_schedule.locked_occurrences` is pinned exactly to its
  current slot.
- Optimizes in two strict, separately-solved phases: (1) minimize
  disruption from `reference_schedule` (a moved-occurrence count), then
  (2) among solutions achieving that same minimal disruption, minimize
  the ordinary soft-preference objective from `solve()`. Genuine
  lexicographic priority, not a weighted sum -- see
  `docs/SCHEDULE_EDITING.md` for the full rationale and mechanics.
- Returns the same `SchedulingResult`/`SolverStatus` shape as `solve()`.
  `metadata` additionally includes `num_moved_occurrences`,
  `num_preserved_occurrences`, `disruption_penalty`,
  `num_logical_occurrences`, and per-phase wall time/raw status fields
  (`phase1_*`, `phase2_*`).

## Acceptance criteria

See `tests/`: the valid fixture must solve to `FEASIBLE`/`OPTIMAL` and
pass the independent verifier; the impossible fixture must be rejected
(either by preflight or by an `INFEASIBLE` CP-SAT result); the full
pytest suite must pass.
