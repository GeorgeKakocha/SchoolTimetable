# Project State

## Current milestone

Phase 1: isolated Python CP-SAT solver proof-of-concept. No web
framework, database, or UI.

## Implemented capabilities

- Typed, OR-Tools-free domain model covering every concept in the product
  spec (school, calendar, teachers/availability, classes/participant
  groups, activities, teaching requirements, block/distribution policies,
  time preferences, resources, reserved blocks, fixed placements).
- Preflight validation catching unknown references, malformed/unsupported
  block patterns, fixed placements on unavailable slots, teacher
  overload, split-group inconsistency, and class occupancy mismatches.
- A CP-SAT model builder enforcing all 12 phase-1 hard constraints and
  scoring 4 soft-constraint categories via centralized weights.
- An independent, from-scratch verifier that re-checks every hard
  constraint from the final schedule alone.
- A deterministic, fully-featured valid fixture (4 classes, 8 teachers,
  10 activities) and two distinct impossible fixtures (one rejected by
  preflight, one passing preflight but INFEASIBLE in CP-SAT).
- A manual PoC runner (`python -m school_timetable.run_poc`) that solves
  the valid fixture, verifies it, and prints a readable weekly grid.

## Test baseline

33 pytest tests, all passing (`.venv/bin/python -m pytest -q`):
unit tests for calendar/period-pair logic, preflight validation, the
independent verifier's own detection ability, the impossible fixtures,
and a 15-assertion end-to-end acceptance suite against the valid fixture
covering every phase-1 acceptance criterion.

## Known limitations

- `LessonBlockPolicy` supports at most one double lesson (block size 2)
  per requirement, with the remainder as singles. Arbitrary multi-block
  patterns (e.g. two doubles a week) are not implemented; attempting one
  is rejected by preflight (`UNSUPPORTED_BLOCK_SIZE`) rather than silently
  mishandled.
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

Await review of this milestone. If approved, the natural next step is
introducing the FastAPI/SQLAlchemy/PostgreSQL layer around this existing
domain model and solver, without modifying solver internals.
