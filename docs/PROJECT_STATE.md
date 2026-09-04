# Project State

## Current milestone

Phase 2B: realistic school-scale solver validation. No product-semantic
or HARD-constraint changes; the only production additions are
`SolverOptions` and richer benchmark metadata. Still an isolated Python
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
- **`SolverOptions`** (Phase 2B; `scheduling/options.py`): `max_time_seconds`,
  `num_search_workers`, `random_seed`. `solve(problem)` without it is
  unchanged from Phase 2A. Result `metadata` now also reports
  `num_conflicts`, `num_branches`, `num_lesson_variables`,
  `num_cp_variables`, `num_cp_constraints`, and (when applicable)
  `best_objective_bound`/`objective_value`.
- **A realistic school-scale fixture generator** (`fixtures/school_scale/`):
  15 classes, 34 teachers, 19 activities, 227 `TeachingRequirement`s, 600
  total class-slot occupancy units/week, proven feasible by solving a
  placement-free version of the problem once (see
  `docs/SCALE_VALIDATION.md`) rather than by hand-guessing. Three
  scenarios: `standard`, `tight` (more unavailability, more fixed
  placements), `impossible` (a genuine capacity-1 gym overcommitment).
- A dedicated benchmark runner, `python -m school_timetable.run_scale_benchmark`,
  reporting compact per-run statistics (not a full timetable by default).

## Test baseline

67 pytest tests total. `pytest -q` (default, slow-marked tests excluded):
**63 passed** in under a second. `pytest -q -m slow`: the 4 school-scale
integration tests (standard/tight solve+verify, impossible-never-valid,
standard 3x stability) -- **4 passed** in ~16s on this machine, since each
invokes CP-SAT twice (once to prove the fixture feasible, once to
benchmark it).

Phase 2A's 52 remain unchanged in behavior; Phase 2B adds 11 fast unit
tests for the fixture generator/consistency check (`test_school_scale_fixture.py`)
and 4 slow integration tests (`test_school_scale_integration.py`).

## Known limitations

- `PREFERRED` intentionally still supports only the Phase-1 shape (at
  most one size-2 block, the rest singles) -- not generalized; see
  `DECISIONS.md` for why.
- No performance bottleneck was found at 15-class/227-requirement scale
  (all scenarios solve to OPTIMAL in ~1-1.7s locally; see
  `docs/SCALE_VALIDATION.md`), so no solver hardening work is queued.
  This has only been validated at this one scale point, not larger ones.
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

Await review of this Phase 2B slice. See `docs/SCALE_VALIDATION.md` for
the explicit readiness recommendation. Candidate next steps: manual
edit/lock/re-optimization, or introducing the FastAPI/SQLAlchemy/PostgreSQL
layer around this existing domain model and solver -- neither has been
started.
