# Project State

## Current milestone

Phase 3A1: backend/database foundation. Adds `config.py`, `persistence/`
(SQLAlchemy engine/session + Alembic, one empty baseline revision), and
`api/` (FastAPI app shell + a real, database-backed `GET /health`) --
infrastructure only. No domain ORM tables, no `application/` layer, no
repository ports/adapters, no business endpoints, no React, and no
change to any existing domain/scheduling/validation/verification
semantics (Phase 2C's 104 tests, 3 demo scripts, and school-scale
benchmark all still pass unmodified). See `docs/ARCHITECTURE.md` for the
locked ports-and-adapters direction Phase 3A2 will build against.

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
- **Manual schedule editing** (Phase 2C; `domain/schedule.py`,
  `scheduling/editing.py`): `Schedule` (entries + lock state),
  `find_logical_occurrence` (the unambiguous move/lock unit -- see
  `docs/SCHEDULE_EDITING.md`), `validate_move`/`apply_move` (a manual
  move is a validated occupancy-preserving swap, never a plain
  relocation), `lock_occurrence`/`unlock_occurrence`. Hardened after a
  pre-commit audit: `validate_move` requires its input `Schedule` to
  already be HARD-valid (`INVALID_SCHEDULE` otherwise), and
  `apply_move(problem, current_schedule, result)` re-validates the
  original `MoveIntent` against `current_schedule` before applying,
  rejecting a stale or now-different plan with `STALE_MOVE_PLAN` instead
  of trusting a detached `MovePlan` -- see "Runtime safety guarantees" in
  `docs/SCHEDULE_EDITING.md`.
- **Re-optimization** (Phase 2C; `scheduling/reoptimize.py`): reuses
  every existing HARD constraint unchanged, adds lock constraints and a
  disruption objective, and uses genuine two-phase lexicographic solving
  (minimize disruption first, provably; only then minimize the ordinary
  soft preferences among equally-disrupted solutions) -- not a weighted
  sum. Reports `num_moved_occurrences`, `num_preserved_occurrences`,
  `disruption_penalty` in `metadata`. Also hardened: independently checks
  its `reference_schedule` argument has valid STRUCTURE/TOPOLOGY (no
  double-booked entries, correct weekly counts, well-formed REQUIRED
  blocks, synchronized splits, consistent merged-group classes, full
  class occupancy) before building disruption groups/locks, returning
  `INVALID_INPUT` (never an unlocked fallback solve) for a malformed one.
  This is deliberately a different question from "is the reference
  *feasible* under the current problem" -- teacher-availability,
  `FixedPlacement`, resource-capacity, `ReservedBlock`, and
  `max_periods_per_day` compliance are excluded from this check, since
  violating exactly one of those (the reference having been valid when
  generated, before the problem changed) is the normal trigger for
  calling `reoptimize` in the first place; the rebuilt CP-SAT model
  enforces all of their current-problem versions regardless.
- A narrated demonstration, `python -m school_timetable.run_editing_demo`,
  walking through generate -> validate a move -> apply -> lock ->
  re-optimize -> verify.
- **Backend/database foundation** (Phase 3A1; `config.py`, `persistence/`,
  `api/`): `Settings`/`get_settings()` reading `DATABASE_URL` from the
  environment/`.env` (PostgreSQL only, never SQLite -- see
  `DECISIONS.md`); a SQLAlchemy `Engine`/`sessionmaker`/`get_session()`
  FastAPI dependency (`persistence/db.py`) and an empty `Base` for future
  ORM models (`persistence/base.py`); Alembic wired to the same
  `DATABASE_URL` with one empty baseline revision applied via
  `alembic upgrade head`; a FastAPI app (`api/main.py`) with
  `GET /health` that genuinely executes `SELECT 1` (200 `{"status":"ok",
  "database":"ok"}` when reachable, 503 `{"status":"error",
  "database":"unreachable"}` -- deliberately generic, no connection
  string/host/exception detail -- when not; never a faked success).
  `docker-compose.yml` provides a local development PostgreSQL 16 with a
  second database for the test suite, bound to `127.0.0.1` only by
  default; the app's `Settings` has no hard-coded credential default
  (required from the environment/`.env`), while `.env.example` and
  `docker-compose.yml` document environment-overridable local-development
  placeholder values, per the credential policy in `DECISIONS.md` #24.
  Live-validated end-to-end this
  session (`docker compose up -d db`, real PostgreSQL 16): both
  databases created, `alembic upgrade head`/`alembic current` against
  the live instance, real 200 and 503 responses (503 body confirmed free
  of the probe's credentials/host), and all 7 `tests_web` tests passing
  with zero skips.

## Test baseline

104 pytest tests total. `pytest -q` (default, slow-marked tests excluded):
**99 passed** in ~2s. `pytest -q -m slow`: **5 passed** in ~24s on this
machine -- the 4 Phase-2B school-scale integration tests plus the
Phase-2C school-scale re-optimization test (each invokes CP-SAT multiple
times: fixture-feasibility witness, reference solve, and the two-phase
re-optimize).

Phase 2B's 67 remain unchanged in behavior; Phase 2C adds 36 fast
move/lock/re-optimize/safety tests (`test_editing_moves.py`,
`test_editing_locks.py`, `test_reoptimize.py`, `test_editing_safety.py`)
and 1 slow school-scale re-optimization test (`test_reoptimize_scale.py`).
`test_editing_safety.py` covers the pre-commit-audit fixes specifically:
stale-`MovePlan` rejection (two scenarios), the normal validate/apply
path on an unchanged schedule, a malformed REQUIRED-block input schedule
rejected by both `validate_move` and `find_logical_occurrence` directly,
and `reoptimize` rejecting a malformed reference schedule.
`test_reoptimize.py` additionally covers, one scenario each, a previously
valid reference being *repaired* (not rejected) when the current problem
newly conflicts with it via `FixedPlacement`, resource capacity,
`ReservedBlock`, or a tightened `max_periods_per_day` -- the same
placement-feasibility-vs-structure distinction as the teacher-
availability scenario.

Separately, `tests_web/` (Phase 3A1; needs the `web` extras, and a
PostgreSQL for the database-backed tests) covers configuration loading
(3 tests, no database needed) and the engine/session/health-check
plumbing (4 tests requiring a live database, which skip cleanly rather
than fail if one isn't reachable). Not part of `pytest -q`'s default
collection -- run explicitly with `pytest -q tests_web`. This suite does
not count toward, or affect, the 104/99/5 figures above. Live-validated
this session against a real `docker compose up -d db` PostgreSQL 16:
**7 collected, 7 passed, 0 skipped.**

## Known limitations

- `PREFERRED` intentionally still supports only the Phase-1 shape (at
  most one size-2 block, the rest singles) -- not generalized; see
  `DECISIONS.md` for why.
- No performance bottleneck was found at 15-class/227-requirement scale
  (all scenarios solve to OPTIMAL in ~1-1.7s locally; see
  `docs/SCALE_VALIDATION.md`), so no solver hardening work is queued.
  This has only been validated at this one scale point, not larger ones.
  Re-optimization at this scale (Phase 2C) also solved to OPTIMAL in
  ~1.6s with only 2 of 562 logical occurrences moved for one deliberate
  new constraint.
- Teacher gap minimization is explicitly out of scope for this milestone.
- A manual move only supports a like-for-like swap (same class-set, same
  block length as the target occupant); anything else is rejected with a
  specific code rather than attempted via a more complex cascade -- see
  `docs/SCHEDULE_EDITING.md`.
- No domain persistence, no application services, no business API
  endpoints, no UI -- Phase 3A1 is infrastructure only; see
  `docs/ARCHITECTURE.md` for what Phase 3A2 adds next.
- Live PostgreSQL validation is now complete (previously the open item
  here): `docker compose up -d db` against real PostgreSQL 16, both the
  `school_timetable` and `school_timetable_test` databases confirmed
  present, `alembic upgrade head`/`alembic current` run against the live
  development database (now at `e2cbe4786a14`, the empty baseline head),
  a real 200 `GET /health` against it, a real 503 against a genuinely
  unreachable database with the response body confirmed free of the
  probe's credentials/host, and `pytest -q tests_web` at 7/7 passed with
  zero skips. Full regression (`pytest -q -m ""` on `tests/`) still
  104/104 passed, and `run_poc.py`/`run_scale_benchmark.py`/
  `run_editing_demo.py` all still run clean -- none of this depends on
  or touches the web/persistence layer.

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

Await final pre-commit review of this Phase 3A1 slice (live-PostgreSQL
validation is now complete -- see "Known limitations" above). Candidate
next step, Phase 3A2: the first domain ORM models
(school/calendar/teachers/requirements), the first repository
`Protocol`s in `application/` alongside the first real use case that
needs them, and a `problem_loader` mapping persisted rows to a
`SchedulingProblem` -- not started.
