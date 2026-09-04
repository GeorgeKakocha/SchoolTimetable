# Solver Contract

## Input

A `SchedulingProblem` (see `src/school_timetable/domain/problem.py`),
containing: school/academic year identity, days, periods, teachers,
teacher availabilities, class sections, participant groups, activities,
teaching requirements, resources, reserved blocks, and fixed placements.
Nothing is hard-coded: day/period counts, teacher counts, subject counts,
etc. are all read from this object.

## Output

A `SchedulingResult`:

- `status: SolverStatus` -- one of `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`,
  `INVALID_INPUT`, `ERROR`.
- `entries: tuple[ScheduleEntry, ...]` -- populated only for
  `OPTIMAL`/`FEASIBLE`. Each entry carries activity, teacher (if any),
  participant group (if any), class sections occupied, day, period, and
  resource (if any).
- `total_soft_penalty: int` -- the objective value (0 if no soft terms
  applied).
- `validation_errors: tuple[ValidationError, ...]` -- populated only for
  `INVALID_INPUT`.
- `metadata: dict` -- solver wall time and raw CP-SAT status name.

## HARD constraints (must always hold)

1. Teacher non-overlap.
2. Participant-group non-overlap.
3. Full class occupancy: every main class has exactly one activity in
   every instructional period (pilot-fixture requirement).
4. Every `TeachingRequirement` receives exactly its `weekly_periods`.
5. Teacher `UNAVAILABLE` slots are never used.
6. Reserved/club blocks: ordinary lessons are forbidden in a class's
   reserved slots; the reserved activity occupies them instead.
7. `REQUIRED` double lessons: same day, consecutive instructional
   periods, never crossing a structural break (e.g. lunch).
8. Parallel split groups: branches sharing a `split_group_id` are always
   scheduled in identical slots, with the parent class(es) occupied once.
9. Merged groups occupy every underlying class simultaneously.
10. Resource capacity is never exceeded in any slot.
11. Fixed placements are always honored.
12. `max_periods_per_day`, when set on a requirement, is respected.

## SOFT constraints (minimized, never silently violate a HARD one)

1. Teacher `PREFER_NOT` slot usage.
2. Non-preferred period usage (`TimePreference`).
3. A `PREFERRED` double lesson not being formed.
4. `min_distinct_days` shortfall.

Weights are centralized in `scheduling/weights.py` as three tiers
(`LOW`/`MEDIUM`/`HIGH`) mapped to integers -- no magic numbers scattered
through solver code.

## Preflight validation

`validation/preflight.py` runs before CP-SAT and rejects (as
`INVALID_INPUT`) obviously broken input: unknown references (teacher,
activity, participant group, class, resource, requirement, slot),
malformed or unsupported block patterns, a fixed placement landing on a
teacher's `UNAVAILABLE` slot, a teacher required for more weekly periods
than they have usable slots, inconsistent split-group branches, and a
class occupancy total that cannot possibly reach full occupancy. It never
looks at CP-SAT.

## Independent verification

`verification/verifier.py` re-derives every hard constraint directly from
the final `ScheduleEntry` list and the plain domain model -- it does not
read CP-SAT variables and does not assume the solver's constraints were
encoded correctly. If a solver bug exists, this is what catches it.

## Acceptance criteria

See `tests/`: the valid fixture must solve to `FEASIBLE`/`OPTIMAL` and
pass the independent verifier; the impossible fixture must be rejected
(either by preflight or by an `INFEASIBLE` CP-SAT result); the full
pytest suite must pass.
