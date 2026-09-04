# Project State

## Current milestone

Phase 2A: solver hardening -- generalized REQUIRED lesson-block patterns,
plus closing two Phase-1 test-coverage gaps. Still an isolated Python
CP-SAT solver, no web framework, database, or UI.

## Implemented capabilities

- Typed, OR-Tools-free domain model covering every concept in the product
  spec (school, calendar, teachers/availability, classes/participant
  groups, activities, teaching requirements, block/distribution policies,
  time preferences, resources, reserved blocks, fixed placements).
- Preflight validation catching unknown references, malformed block
  patterns, fixed placements on unavailable slots, teacher overload,
  split-group inconsistency, and class occupancy mismatches.
- A CP-SAT model builder enforcing all 12 phase-1 hard constraints and
  scoring 4 soft-constraint categories via centralized weights.
- **REQUIRED lesson-block patterns are now generic** (Phase 2A): any
  multiset of positive block lengths summing to `weekly_periods` (e.g.
  `(2, 1, 1, 1)`, `(2, 2)`, `(3, 1)`), each on its own day, each block of
  length > 1 a genuinely consecutive run that never crosses a structural
  break. Enforced exactly by CP-SAT via `domain.calendar.period_windows`;
  rejected explicitly by preflight when unplaceable. `PREFERRED`
  intentionally still uses the narrower Phase-1 shape (see `DECISIONS.md`).
- An independent, from-scratch verifier that re-checks every hard
  constraint -- including the generic REQUIRED pattern shape -- from the
  final schedule alone, with no CP-SAT dependency.
- A deterministic, fully-featured valid fixture (4 classes, 8 teachers,
  10 activities) and two distinct impossible fixtures (one rejected by
  preflight, one passing preflight but INFEASIBLE in CP-SAT).
- A manual PoC runner (`python -m school_timetable.run_poc`) that solves
  the valid fixture, verifies it, and prints a readable weekly grid.

## Test baseline

52 pytest tests, all passing (`.venv/bin/python -m pytest -q`): the
Phase-1 baseline (updated from 33 to 35 -- one test that asserted the old
REQUIRED size-3 restriction was repurposed to assert the still-restricted
PREFERRED behavior, plus a new REQUIRED-now-accepts-size-3 test and a
PREFERRED-multi-double-rejected test), plus 5 focused occupancy tests
(full-class-occupancy missing-slot/double-booking detection, and
split-group occupancy non-double-counting with a negative-control
contrast case) and 12 focused REQUIRED-block-pattern tests (`[2,2]`,
`[2,1,1,1]`, `[3,1]` end-to-end solves; boundary-crossing rejection at
both preflight and verifier level; sum-mismatch, non-positive-length,
too-many-blocks, max-periods-per-day, and unplaceable-length preflight
rejections; and a malformed-multi-block verifier detection test).

## Known limitations

- `PREFERRED` intentionally still supports only the Phase-1 shape (at
  most one size-2 block, the rest singles) -- not generalized alongside
  REQUIRED in this slice; see `DECISIONS.md` for why.
- No performance tuning has been done; the valid fixture (4 classes, ~24
  requirements, 160 lesson-instances) solves to proven optimality in well
  under a second, so this has not been a concern yet.
- Teacher gap minimization is explicitly out of scope for this milestone.
- No persistence, API, or UI -- by design, per `DECISIONS.md`.

## Development note (for future maintainers)

During development, the first CP-SAT encoding of `REQUIRED`/`PREFERRED`
double lessons enforced "exactly one consecutive pair exists" and "weekly
total is correct" but did not cap how many periods of that requirement
could land on any other single day. CP-SAT exploited this and returned an
OPTIMAL schedule that stacked a third period onto the double lesson's day.
The independent verifier caught it immediately (the solver's own
FEASIBLE/OPTIMAL claim was not trusted), which is exactly the scenario the
verifier exists for. The fix adds a per-day cap of 1 lesson unless that
day is hosting the double. This is recorded here as a concrete example of
why verification is a hard requirement, not a formality.

## Next step

Await review of this Phase 2A slice. Candidate next steps: Phase 2B scale
testing, or introducing the FastAPI/SQLAlchemy/PostgreSQL layer around
this existing domain model and solver -- neither has been started.
