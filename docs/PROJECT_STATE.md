# Project State

## Current milestone

Phase 3A2.4: read-only scheduling configuration API. Adds
`GET /schools/{school_id}/years/{year_id}/config` -- the first
domain/business FastAPI endpoint. Path IDs are natural/domain IDs
(`School.id`/`AcademicYear.id`, i.e. their `natural_id` columns), never
surrogate ones. Wired through the locked ports-and-adapters composition
root (`api/dependencies.py`, the one place importing both
`application/` and `persistence/`): the route depends on
`application.ports.SchedulingProblemRepository` (the Protocol), never
on the concrete `SqlAlchemySchedulingProblemRepository`. Returns an
explicit, hand-designed `SchedulingConfigResponse` (`api/schemas.py`)
built by a pure `api/serializer.py` mapper -- never an ORM row, never
`dataclasses.asdict()`, never a persistence surrogate ID or ORM
`ordinal`. `application.errors.SchedulingProblemNotFoundError` maps to
a generic 404 body, identically whether the school or the academic year
is the part that doesn't resolve. Configuration-only: no solver/preflight
invocation from the route (that proof already exists in Phase 3A2.3), no
`POST /generate`/`POST /solve`/`GET /schedule`, still no domain ->
persistence write path in production code, no
`Schedule`/`ScheduleVersion`/`ScheduleEntry` persistence, no React, and
no change to Phase 3A2.1's schema/migration (still `8cdd513e16da`) or to
Phase 3A2.2/3A2.3's mappers/repository, or to any existing
domain/scheduling/validation/verification semantics (Phase 2C's 104
tests, 3 demo scripts, and school-scale benchmark all still pass
unmodified). See `DECISIONS.md` #26-30 for the full locked schema,
mapper-boundary, repository-port, and public-API-contract rules.

**Phase 3A3 status: design/owner decisions are complete, with ZERO
remaining owner decisions; implementation has NOT started.** All five
Phase 3A3 owner decisions are locked (one canonical `Schedule` per
School+AcademicYear; Generate is initial-generation-only, conflicting
409 if a schedule already exists; the read API is a generic flat
`GET .../schedule/active`, no React projection yet; the solve runs with
no DB session/connection open at all, by strict phase separation; a
fixed, minimal solver/audit metadata set is persisted), and the
previously-open HTTP mapping question is now also locked
(`InvalidConfiguration` -> 422, `Infeasible` -> 409, distinguished from
`ScheduleAlreadyExists`'s own 409 by a stable `code` field). The full
schema/application/API design, including `schedule_entry.ordinal` for
exact tuple-order round-trip and the concurrent double-Generate race
handling, is recorded in `DECISIONS.md` #31. No
`schedule`/`schedule_version`/`schedule_entry`/`locked_occurrence` ORM
model, migration, repository, service, or API route exists yet. The
next implementation slice is **Phase 3A3.1 only** (schedule/version
persistence schema + migration) -- not started.

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
- **Persisted scheduling-configuration schema** (Phase 3A2.1;
  `persistence/models.py`, Alembic revision `8cdd513e16da`): 17
  SQLAlchemy ORM tables persisting the complete current
  `SchedulingProblem` input surface as one `academic_year_id`-scoped
  snapshot -- see `DECISIONS.md` #26 for the full locked rules (BIGINT
  identity surrogate PKs never exposed outside `persistence/`;
  `natural_id` columns carrying every domain string ID verbatim;
  same-academic-year composite foreign keys enforced by PostgreSQL
  itself, not just Python, e.g. `FOREIGN KEY (academic_year_id,
  teacher_id) REFERENCES teacher (academic_year_id, id)`; no JSONB --
  `block_sizes`/`preferred_period_indexes` are `SMALLINT[]`,
  `TimePreference` is a normalized child table, every enum is
  `TEXT + CHECK`; exact tuple order preserved via `Day.idx`/`Period.idx`
  or an explicit `ordinal` column with `UNIQUE(academic_year_id,
  ordinal)`; aggregate-oriented delete semantics -- whole-snapshot and
  true-owned-child edges `CASCADE`, every other cross-entity reference
  (including the two optional ones) `RESTRICT`, never `SET NULL`).
  ORM models and the Alembic migration were produced together in one
  slice (autogenerate against `Base.metadata`, hand-reviewed); a
  second autogenerate run against the migrated database produced an
  empty diff (no drift). Migration reversibility (baseline -> upgrade
  -> downgrade -1 -> upgrade) proven against the real PostgreSQL *test*
  database, then applied once (no destructive testing) to the
  development database. 10 focused `tests_web` tests prove, against
  real PostgreSQL: valid same-year inserts succeed; cross-academic-year
  references are rejected at the database level; `natural_id`
  uniqueness is per-academic-year (same ID across two years is
  allowed); `Day.idx`/`Period.idx` duplicates are rejected;
  invalid enum values and non-positive `weekly_periods`/`capacity` are
  rejected; an owned-child `CASCADE` delete actually removes the child
  row; a cross-entity `RESTRICT` blocks a destructive sibling delete
  for both a required and an optional reference (proving no silent
  `SET NULL`); ordered-child-table ordinal/membership uniqueness is
  enforced; and deleting the root `AcademicYear` cascades the complete
  interconnected snapshot (proving the whole-graph CASCADE/RESTRICT
  interaction resolves correctly, not just isolated pairwise edges)
  while the parent `School` survives untouched.
- **Persistence -> domain mapping** (Phase 3A2.2; `persistence/mappers.py`):
  pure functions mapping already-loaded Phase 3A2.1 ORM rows to frozen
  `domain/` objects -- no `Session`, no query, no engine, deterministic.
  A `NaturalIdLookup` context (built once from whatever rows the caller
  already fetched, via `.build()`) resolves every sibling surrogate FK
  to its natural ID; an unresolved surrogate fails immediately with a
  clear `KeyError`, never silently returning the surrogate or inventing
  an ID. Every mapper reconstructs exact tuple order from `ordinal`
  columns (never database return order): `ParticipantGroup.class_sections`,
  `TeachingRequirement.time_preferences`, `ReservedBlock.class_sections`,
  `ReservedBlock.slots`. `TeachingRequirement` mapping reconstructs
  `LessonBlockPolicy`/`DistributionPolicy`/`ResourceRequirement` exactly,
  including a real cross-check against `build_valid_fixture()`'s actual
  `math_8a` object for full-equality proof; `TimePreference.preferred_periods`
  round-trips `Period.index` integers verbatim (never reinterpreted as
  `Period.id`). Only persistence -> domain exists; domain -> persistence
  remains intentionally absent from production code (Decision #28) --
  writing requires whole-graph natural-id -> surrogate-id resolution,
  deferred to a test-only aggregate writer in Phase 3A2.3. 14 new pure
  unit tests (no live database) in `tests_web/test_persistence_mappers.py`
  cover every mapper, both `Activity.kind` enum values, all three
  `BlockPolicyMode`s (including a `[3, 1]` REQUIRED pattern), nullable
  `DistributionPolicy` fields, multiple out-of-order `TimePreference`
  rows, `ResourceRequirement` present/absent, `split_group_id`
  preserved verbatim, out-of-order `ReservedBlock` children, optional
  `ReservedBlock.teacher_id` `None`, `FixedPlacement` `TimeSlot`
  reconstruction, and an unresolved surrogate reference raising a clear
  `KeyError`. No `application/` repository Protocol, no config-read
  API, and no `SchedulingProblem` aggregate loader exist yet (Phase
  3A2.3+ -- see `DECISIONS.md` #28); `mappers.py` never imports
  `fixtures/`, `application/`, or `api/`.
- **DB-backed `SchedulingProblem` repository** (Phase 3A2.3;
  `application/ports.py`, `application/errors.py`,
  `persistence/problem_repository.py`): the first real `application/`
  package. `SchedulingProblemRepository` is a `Protocol` with exactly
  one method, `load_by_school_and_year(school_natural_id,
  academic_year_natural_id) -> SchedulingProblem` -- no generic CRUD.
  `SchedulingProblemNotFoundError` (natural IDs only, never a
  SQLAlchemy exception or surrogate ID) is raised identically whether
  the school itself is unknown or the school is known but the academic
  year is not. `application/` imports only `domain/` -- no SQLAlchemy,
  no `persistence/`, no FastAPI, no `fixtures/`.
  `SqlAlchemySchedulingProblemRepository` implements the port
  structurally (no inheritance): resolves `School`/`AcademicYear` by
  natural ID first, then issues one explicit `SELECT ... WHERE
  academic_year_id = :id` per scoped table (no ORM `relationship()`
  navigation, no lazy-loading), builds a `NaturalIdLookup` from the
  fetched rows, groups child rows by parent surrogate ID in plain
  Python, and calls Phase 3A2.2's mappers -- reimplementing none of
  preflight/solver/verifier reasoning itself. Every top-level tuple is
  sorted explicitly before mapping (`Day`/`Period` by `idx`, everything
  else by `ordinal`), never assumed from database return order.

  Proven end-to-end against real PostgreSQL: a TEST-ONLY aggregate
  writer (`tests_web/support/problem_writer.py` -- domain ->
  persistence, identity-resolution-aware, living only under
  `tests_web/`, never imported or referenced by `persistence/`,
  `application/`, or `api/`, and explicitly not a production repository
  "save" method)
  writes the complete `build_valid_fixture()` graph in dependency
  order, assigning every `ordinal` from `enumerate(...)`. The
  production repository then reads it back, and the result is
  asserted **deeply equal** to the original in-memory
  `SchedulingProblem` -- exact tuple order included, not a partial
  comparison. The DB-loaded problem also passes `run_preflight` with no
  errors, `solve()`s to the same `SolverStatus` as the in-memory
  problem, and passes the independent `verify()` -- proving the
  persisted configuration is not a silently-narrowed version of the
  real model. A further test writes two independent snapshots sharing
  every natural ID *except* school/academic-year and confirms each
  loads back only its own rows, never mixing in the other's. 4 new
  integration tests in `tests_web/test_problem_repository.py`.
- **Read-only scheduling configuration API** (Phase 3A2.4;
  `api/schemas.py`, `api/serializer.py`, `api/dependencies.py`,
  `api/config_routes.py`): the first domain/business endpoint,
  `GET /schools/{school_id}/years/{year_id}/config`. `api/schemas.py`
  hand-designs one Pydantic model per current domain concept (`School`,
  `AcademicYear`, `Day`, `Period`, `ClassSection`, `ParticipantGroup`,
  `Teacher`, `TeacherAvailability`, `Activity`, `Resource`,
  `TeachingRequirement` with its nested `LessonBlockPolicy`/
  `DistributionPolicy`/`TimePreference`/`ResourceRequirement`,
  `ReservedBlock`, `FixedPlacement`, `TimeSlot`) -- deliberately never a
  generic `dataclasses.asdict()` dump, so a future domain field cannot
  leak into the public contract without an explicit decision to add it
  here too. `api/serializer.py`'s `config_response_from_problem` is a
  pure, field-by-field mapper (no SQLAlchemy, no `persistence.models`,
  no `fixtures/`) that never re-sorts anything itself -- exact tuple
  order is inherited verbatim from the already-proven Phase 3A2.3
  repository. `api/dependencies.py` is the composition root (the one
  place importing both `application/` and `persistence/`):
  `get_scheduling_problem_repository` wraps the existing
  `persistence.db.get_session` FastAPI dependency (one `Session` per
  request, always closed after, no global long-lived `Session`) and
  constructs `SqlAlchemySchedulingProblemRepository` from it; the route
  itself is typed against `application.ports.SchedulingProblemRepository`
  (the Protocol), never the concrete class.
  `application.errors.SchedulingProblemNotFoundError` maps to a generic
  404 body (`{"detail": "Scheduling configuration not found"}`)
  identically whether the school or the academic year doesn't resolve
  -- no other exception is caught, so an unexpected failure still
  surfaces as a 500, never masquerading as a not-found result.

  Proven against real PostgreSQL: the same TEST-ONLY
  `write_scheduling_problem` writer seeds `build_valid_fixture()`, and
  the FastAPI `get_session` dependency is overridden (via
  `app.dependency_overrides`, the same pattern already used by
  `test_health.py`) to hand the route the identical
  Session/transaction the test used to seed -- never a separate,
  independently-committed one. The full JSON response is asserted
  **equal** to the same serializer's output built directly from the
  original in-memory `SchedulingProblem` (a complete-contract proof, not
  scattered field checks), representative multi-item collections
  (`ParticipantGroup.class_sections`, `block_sizes`,
  `TimePreference.preferred_periods`, `ReservedBlock.class_sections`/
  `.slots`) are checked for exact order, a recursive walk of the whole
  response confirms no `academic_year_id`/`ordinal` key and no
  non-string `id`/`*_id` value appears anywhere, both an unknown school
  and a known-school/unknown-year both return the identical 404 body,
  and one HTTP-level test proves two overlapping-natural-ID snapshots
  never bleed into each other's response. 5 new integration tests
  (`tests_web/test_config_api.py`) plus 4 DB-free serializer unit tests
  (`tests_web/test_api_serializer.py`).

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

Separately, `tests_web/` (Phase 3A1+; needs the `web` extras, and a
PostgreSQL for the database-backed tests) covers configuration loading
(3 tests, no database needed), the engine/session/health-check plumbing
(4 tests requiring a live database, which skip cleanly rather than fail
if one isn't reachable), 10 persistence-schema constraint tests
(Phase 3A2.1) exercising the locked schema directly against real
PostgreSQL (same-year valid inserts, cross-academic-year rejection,
natural-ID/ordinal uniqueness, enum/positive-value CHECK constraints,
CASCADE/RESTRICT delete semantics, and the whole-snapshot root delete),
14 pure persistence -> domain mapper unit tests (Phase 3A2.2,
`test_persistence_mappers.py`) needing no live database at all,
4 repository round-trip/preflight/solve/verify/scope-isolation
integration tests (Phase 3A2.3, `test_problem_repository.py`) against
real PostgreSQL, 4 DB-free serializer unit tests (Phase 3A2.4,
`test_api_serializer.py`), and 5 config-API integration tests (Phase
3A2.4, `test_config_api.py`) against real PostgreSQL through a real
FastAPI `TestClient`. Not part of `pytest -q`'s default collection --
run explicitly with `pytest -q tests_web`. This suite does not count
toward, or affect, the 104/99/5 figures above. Live-validated this
session against a real `docker compose up -d db` PostgreSQL 16:
**44 collected, 44 passed, 0 skipped.**

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
- A persisted schema (Phase 3A2.1), persistence -> domain mappers
  (Phase 3A2.2), a proven DB-backed `SchedulingProblemRepository`
  (Phase 3A2.3), and now a read-only `GET /config` endpoint (Phase
  3A2.4) all exist, but there is still no business/domain API beyond
  that one read endpoint, no UI, and still no domain -> persistence
  write path anywhere in production code (the test-only aggregate
  writer under `tests_web/support/` remains test-only, per Decision
  #28). No solver/schedule-generation endpoint exists either
  (`POST /generate`/`POST /solve`/`GET /schedule` are all future work) --
  Phase 3A2.4 is deliberately configuration-read-only. See
  `docs/ARCHITECTURE.md` and `DECISIONS.md` #27-30 for exactly what
  comes next.
- Phase 3A3's locked design (`DECISIONS.md` #31) accepts a known
  limitation: because Phase 3A2 has no configuration versioning, a
  future historical `ScheduleVersion` will resolve
  teacher/activity/policy fields against the *current* configuration,
  not necessarily the configuration that was true when that version
  was generated. Accepted for now because no production
  configuration-write path exists yet to make the two actually differ;
  revisit only if/when configuration editing is introduced.
- Live PostgreSQL validation (Phase 3A1) remains complete:
  `docker compose up -d db` against real PostgreSQL 16, both the
  `school_timetable` and `school_timetable_test` databases confirmed
  present, a real 200 `GET /health` against it, a real 503 against a
  genuinely unreachable database with the response body confirmed free
  of the probe's credentials/host. Full regression (`pytest -q -m ""` on
  `tests/`) still 104/104 passed, and `run_poc.py`/
  `run_scale_benchmark.py`/`run_editing_demo.py` all still run clean --
  none of this depends on or touches the web/persistence layer. Both
  databases are now migrated to Phase 3A2.1 head (`8cdd513e16da`);
  migration reversibility was proven against the test database only
  (never destructively tested against the development one).
- Non-blocking dependency-maintenance note: `tests_web`'s FastAPI
  `TestClient`-based tests (Phase 3A2.4's `test_config_api.py`,
  Phase 3A1's `test_health.py`) emit a
  `StarletteDeprecationWarning` ("Using `httpx` with
  `starlette.testclient` is deprecated; install `httpx2` instead").
  Not a Phase 3A2.4 blocker; addressing it is a future dependency
  bump, not a code change to this phase's endpoint.

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

Phase 3A2.4 is closed (merged to `main`). Phase 3A3's design and all
five owner decisions are locked -- see `DECISIONS.md` #31 for the
complete schema, application-service, port, and API design.

**Phase 3A3.1 is CLOSED and merged to `main`** (commit `4447b10`). The
four persistence tables it adds -- `schedule`, `schedule_version`,
`schedule_entry`, `locked_occurrence` -- and one Alembic migration
(`4681f7a362bd`, on top of `8cdd513e16da`) are now part of authoritative
`main`, exactly as specified in `DECISIONS.md` #31. Proven against real
PostgreSQL: migration upgrade/downgrade/upgrade, autogenerate no-drift,
and 15 focused schema tests, including the required direct-delete-
rejected and whole-snapshot root-cascade proofs, all passed.

**Phase 3A3.2 is CLOSED and merged to `main`** (commit `bab831e`). It
adds: `application.schedule_models.ActiveScheduleVersion` (plain frozen
dataclass); `application.ports.ScheduleVersionRepository`
(`get_active_schedule`/`persist_initial_version`, no generic CRUD) and
`application.errors.ScheduleAlreadyExistsError`; `persistence.mappers`'s
`schedule_entry_to_domain`/`locked_occurrence_to_domain` (extending
`NaturalIdLookup` with a `reserved_blocks` lookup) re-deriving each
entry's `activity_id`/`teacher_id`/`participant_group_id`/`resource_id`/
`class_sections` from the referenced
`TeachingRequirement`/`ReservedBlock`/`ParticipantGroup`, exactly as
Decision #31 specifies; `persistence.schedule_repository.
SqlAlchemyScheduleVersionRepository`, a session-factory-backed adapter
whose `persist_initial_version` writes `Schedule` + `ScheduleVersion` 1
+ `ScheduleEntry` rows + the active-version pointer atomically; and
`persistence.problem_repository.SessionFactorySchedulingProblemRepository`,
a session-factory-backed second implementation of the existing
`SchedulingProblemRepository` Protocol for future `GenerateScheduleService`
use, which does not change or replace `SqlAlchemySchedulingProblemRepository`'s
existing session-bound `/config` read path. Proven against real
PostgreSQL: exact DB round-trip with tuple order preserved, initial
schedule/version persistence is atomic, only the
`uq_schedule_academic_year_id` constraint translates to
`ScheduleAlreadyExistsError` (every other integrity violation still
propagates as a genuine defect) -- and that clean application exception
exposes no persistence exception through `__cause__`/`__context__`, only
the natural school/year IDs. A persisted `Schedule` with no active
version (or an unresolvable active pointer) is distinguished from "no
schedule yet": it raises `persistence.schedule_repository.
CorruptScheduleStateError`, an internal defect, never `None` and never
`ScheduleAlreadyExistsError`. 11 focused tests
(`tests_web/test_schedule_repository.py`) cover all of the above plus
not-found handling, session-close tracking, and a `SchedulingProblem`
round-trip through the generation-safe repository. No migration/schema
change, no `GenerateScheduleService`, no API route, no solver/verifier
change.

**Phase 3A3.3 is CLOSED and merged to `main`** (commit `37b85c2`). It
adds `application.generate_schedule_service.
GenerateScheduleService`, the first genuine `application/` orchestration
service, which composes only the existing `SchedulingProblemRepository`/
`ScheduleVersionRepository` ports with the existing preflight
validator, CP-SAT solver, and independent verifier -- never SQLAlchemy,
persistence concrete adapters, or FastAPI. `generate(...)` runs the
locked order: existing-schedule precheck (`get_active_schedule`) ->
load a fully detached `SchedulingProblem` -> explicit preflight ->
solve -> (`OPTIMAL`/`FEASIBLE` only) independent verify -> atomic
`persist_initial_version`, returning exactly the `ActiveScheduleVersion`
the repository gives back. New application-owned outcomes in
`application.errors`: `InvalidSchedulingConfigurationError` (preflight
failure, carrying the validator's own `ValidationError` tuple) and
`ScheduleInfeasibleError` (CP-SAT proved `INFEASIBLE`); two new
internal-only defects local to the service module,
`ScheduleGenerationError` (solver `ERROR`) and
`ScheduleVerificationFailedError` (solver claimed success but the
independent verifier disagreed) -- neither is ever persisted, retried,
or presented as one of the public outcomes. A losing concurrent
double-generate still surfaces the existing `ScheduleAlreadyExistsError`
unchanged, from `persist_initial_version`'s own constraint translation,
with no catch/re-wrap/retry in the service. Proven with 9 pure
orchestration tests (`tests/test_generate_schedule_service.py`, fakes
for both repository ports, no database) covering every branch, plus 4
real-PostgreSQL/real-solver/real-verifier integration tests
(`tests_web/test_generate_schedule_service_integration.py`): a full
generate -> persist -> reload round trip (exact entry order, exact
metadata, second generate rejected without a second version), and three
dedicated proofs that invalid configuration, a genuinely infeasible
valid problem, and a (monkeypatched) verifier failure each leave every
schedule table empty. The integration suite also proves operationally,
via a session-open tracker wrapped around preflight/solve/verify, that
no repository-owned `Session` is open during that window -- the
DB-free boundary Owner Decision 4 requires. No migration/schema change,
no API route, no solver/verifier semantic change -- **Phase 3A3.4 has
NOT started.**

**Phase 3A3.4's HTTP contract is now fully locked** (`DECISIONS.md` #31,
"Phase 3A3.4 final HTTP contract details") -- zero remaining owner
decisions, implementation itself still not started. Locked: `POST
/schools/{school_id}/years/{year_id}/schedule/generate` returns `201
Created` on success (no `Location` header), body exactly
`version_number`/`solver_status`/`total_soft_penalty`/`created_at`/
`is_active` -- no entries, no `wall_time_seconds`, no `random_seed`, no
surrogate IDs; it has no request body, and does not expose
`SolverOptions` (`max_time_seconds`/`num_search_workers`/`random_seed`)
over HTTP -- `GenerateScheduleService`'s own optional `solver_options`
parameter is unchanged and unaffected. `GET
/schools/{school_id}/years/{year_id}/schedule/active` returns the same
version-summary fields plus `entries` (flat, ordered by persisted
`schedule_entry.ordinal`, itself never public) when a `Schedule` exists;
for a valid school/year with no `Schedule` generated yet it returns
`404` with `{"detail": "Active schedule not found"}` -- distinct from,
but the same status and no-`code` shape as, the existing unknown-config
`404` (`{"detail": "Scheduling configuration not found"}`); no new
stable `code` (e.g. `SCHEDULE_NOT_FOUND`) is introduced -- the stable
`code` set remains exactly `INVALID_CONFIGURATION`/
`SCHEDULE_INFEASIBLE`/`SCHEDULE_ALREADY_EXISTS`.

**Phase 3A3.4 is CLOSED and merged to `main`** (commit `50e1c1f`).
`api/schedule_routes.py`'s `POST
/schools/{school_id}/years/{year_id}/schedule/generate` (success `201
Created`, no request body, no public `SolverOptions`) and `GET
/schools/{school_id}/years/{year_id}/schedule/active` (the locked flat,
natural-ID schedule contract) now exist on `main`, implementing the
locked contract above exactly: `api/schemas.py` gains
`GenerateScheduleResponse`/`ActiveScheduleResponse`/
`ScheduleEntryResponse`/`ValidationDiagnosticResponse`/
`InvalidConfigurationResponse`/`GenerationErrorResponse` (hand-written,
no domain/persistence imports); `api/serializer.py` gains pure
field-by-field serializers from `ActiveScheduleVersion`/`ScheduleEntry`/
`ValidationError` to those response models -- no DB query, no
scheduling logic recreated, exact entry order preserved; `api/dependencies.py`
gains `get_schedule_version_repository`/`get_generate_schedule_service`,
both session-factory-backed against the existing `SessionLocal` (never
`Depends(get_session)`), so `GenerateScheduleService` never receives a
request-scoped `Session` across a solve -- the existing
`get_scheduling_problem_repository`/`/config` dependency is unchanged.
Both new routes depend only on `application/` ports/services, never a
concrete `persistence/` class directly. Error mapping implemented
exactly as locked: `SchedulingProblemNotFoundError` -> 404 (existing
`/config`-identical body); GET's "no `Schedule` yet" -> its own distinct
404; `ScheduleAlreadyExistsError`/`ScheduleInfeasibleError` -> 409 with
their stable `code`; `InvalidSchedulingConfigurationError` -> 422 with
`code` plus the validator's own diagnostics in original order;
`ScheduleGenerationError`/`ScheduleVerificationFailedError`/
`persistence.schedule_repository.CorruptScheduleStateError`/any other
unexpected exception are deliberately NOT caught in the route and reach
FastAPI's normal generic-500 behavior, never leaking internal detail.
Proven with 5 new DB-free serializer tests
(`tests_web/test_api_serializer.py`) and 11 new real-PostgreSQL/
real-solver/real-verifier HTTP integration tests
(`tests_web/test_schedule_api.py`) covering the full success/failure
matrix (POST success/no-request-body/duplicate/invalid-config/
infeasible/internal-solver-error/verifier-failure, GET
success/unknown-config/no-schedule-yet/corrupt-state), each internal-500
case proving the injected secret text never reaches the response body.
Existing `/config` tests (`test_config_api.py`) pass unchanged. No
migration/schema change, no solver/verifier semantic change, no
application/persistence contract change was required.

**Phase 3A3 as a whole is CLOSED.** All four slices are complete and
merged to `main`:

- **3A3.1** -- `schedule`/`schedule_version`/`schedule_entry`/
  `locked_occurrence` persistence schema + Alembic migration
  (`4681f7a362bd`).
- **3A3.2** -- schedule persistence mapping (`persistence/mappers.py`)
  and repository adapters (`ScheduleVersionRepository`,
  `SqlAlchemyScheduleVersionRepository`,
  `SessionFactorySchedulingProblemRepository`).
- **3A3.3** -- `GenerateScheduleService` application orchestration
  (existing-schedule precheck -> detached config load -> preflight ->
  solve -> independent verify -> atomic initial-version persistence).
- **3A3.4** -- the HTTP generation/read API (`POST .../schedule/generate`,
  `GET .../schedule/active`) closing this final report.

**Phase 3B's first-view owner decisions are now locked** (`DECISIONS.md`
#32) -- **implementation has NOT started.** Locked: the backend owns
the class-timetable projection (React never reconstructs it from
`/config` + `/schedule/active` itself -- proven necessary because one
`ClassSection`/day/period can genuinely hold more than one simultaneous
`ScheduleEntry`, e.g. a German/Russian split, empirically confirmed by
solving `fixtures/valid_fixture.py`); the first new route is
`GET .../schedule/active/classes/{class_section_id}` (read-only,
natural IDs, its own distinct code-less 404s for unknown class/no active
schedule, no new stable error code); a timetable cell is zero-or-more
entries, never exactly one, and parallel entries render as distinct
visible sub-entries, never collapsed or hidden; the first browser page
is exactly one active-timetable page (class selector + grid + loading/
no-schedule/error states) with Generate/school-selector/year-selector/
teacher-view/history/editing/auth/dashboards all explicitly excluded;
school/year are pilot-fixed through one replaceable configuration point
(never scattered literal IDs) while `ClassSection` is selected
dynamically; calendar rows/columns are derived from `/config`'s
days/periods (ordered by `.index`, periods filtered to
`is_instructional`), never hard-coded 5x8; the projection logic lives in
the backend application layer, never React/ORM/repository-SQL/ad-hoc
serializer code; local dev uses a Vite proxy to the existing FastAPI
server, no CORS middleware; and the first frontend dependency set is
exactly React/TypeScript/Vite (no Router, no state-management library,
no component library yet; Vitest/RTL introduced with the first real
component). **3B.2/3B.3/3B.4 must not start early.**

**Phase 3B.1 is CLOSED and merged to `main`** (commit `8160e47`). Adds
`application.class_timetable_models`
(`ClassTimetableEntry`/`DayHeader`/`ClassTimetableCell`/
`ClassTimetableRow`/`ClassTimetableView`, plain frozen dataclasses) and
`application.class_timetable_service.ClassTimetableService`, composed
from the existing `SchedulingProblemRepository`/`ScheduleVersionRepository`
ports unchanged -- no port/persistence/schema change. `project(...)`
resolves the requested class from config (raising the new
`application.errors.ClassSectionNotFoundError` if unknown), returns
`None` if no `Schedule` has been generated yet, and otherwise groups
`ActiveScheduleVersion.entries` by `(day_id, period_id)` for the
requested class -- membership is exactly
`class_section_id in entry.class_sections`, entries are never
deduplicated (parallel split-group entries both survive in one cell),
and within-cell order exactly preserves persisted entry order. Days are
ordered by `Day.index`; periods are filtered to `is_instructional` and
ordered by `Period.index` -- never hard-coded. Display names
(activity/teacher/participant-group) are resolved via strict lookup
against `SchedulingProblem`'s own tables -- a missing referenced ID
raises (`KeyError`), never silently serializes as blank. `api/schemas.py`
gains `DayHeaderResponse`/`ClassTimetableEntryResponse`/
`ClassTimetableCellResponse`/`ClassTimetableRowResponse`/
`ClassTimetableResponse`; `api/serializer.py` gains a pure
`class_timetable_response_from_view` (no filtering/grouping/name
resolution -- that's already done); `api/dependencies.py` gains
`get_class_timetable_service`, session-factory-backed against
`SessionLocal` exactly like `get_generate_schedule_service`; the new
route, `GET /schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}`,
lives in the existing `api/schedule_routes.py` and maps
`SchedulingProblemNotFoundError`/`None`/`ClassSectionNotFoundError` to
three distinct, code-less 404 bodies, leaving any other exception to
FastAPI's generic 500. Proven with 11 new pure application tests
(`tests/test_class_timetable_service.py`, fakes, no database), 2 new
DB-free serializer tests, and 8 new real-PostgreSQL/real-solver/
real-verifier HTTP integration tests
(`tests_web/test_class_timetable_api.py`): the real German/Russian
split (both branches present in every shared cell, discovered from the
actual solve rather than hard-coded), the real 9a/9b merged history
lesson (appears exactly once in each class's own projection), the real
`club_chess` reserved block (teacher-less, correct in both configured
classes), full response-shape/ordering proofs, and the full 404/500
matrix. No migration/schema change, no solver/verifier change, no
application/persistence port redesign -- the existing
`SchedulingProblemRepository`/`ScheduleVersionRepository` ports were
reused entirely unchanged, and no frontend work was introduced.

**Phase 3B.1 is CLOSED.** `ClassTimetableService` and the dedicated
`GET .../schedule/active/classes/{class_section_id}` route are real,
on `main`, with parallel split entries, merged-group semantics, and
reserved-block semantics all verified against real generated/persisted
data (not synthetic-only).

**Phase 3B.2 is CLOSED and merged to `main`** (commit `bff1c31`).
`frontend/` now exists on `main`: a manually-assembled (not
generator-scaffolded) React 19/TypeScript 7/Vite 8 project with no demo
assets, no router, no state-management library, no component library.
`package-lock.json` is committed on `main` as part of `bff1c31` --
every dependency version is therefore lockfile-pinned for this
frontend foundation, not merely declared as a range. `vite.config.ts`
proxies the `/schools` route prefix to `http://127.0.0.1:8000` for
local development only -- this Vite development-server configuration
is the one and only allowed place an absolute local backend address
appears; no FastAPI `CORSMiddleware` was added, and the frontend's own
API-client/component request-construction code uses relative URLs
exclusively (`src/api/client.ts` never embeds `http://localhost:8000`
or `http://127.0.0.1:8000` itself). `src/config/appConfig.ts` is the
one place `VITE_SCHOOL_ID`/`VITE_ACADEMIC_YEAR_ID` are read/interpreted
(`frontend/.env.example` documents the two placeholders; no real
`.env`/`.env.local` is committed). `src/api/types.ts` hand-mirrors the
minimal `/config` index shape the class selector will need plus Phase
3B.1's exact `ClassTimetableResponse` contract; `src/api/client.ts`
(native `fetch`, relative URLs only) provides
`getSchedulingConfigIndex`/`getClassTimetable`, with a small manual
shape guard reducing the `/config` superset and a typed `ApiError`
preserving the backend's own safe `detail` text (e.g. "Active schedule
not found") with a generic fallback for non-JSON/malformed error
bodies. `App.tsx` is a restrained foundation-only shell ("School
Timetable" + a "being prepared" subtitle) -- it does not yet render a
`ClassSection` selector, the timetable grid, live API data, a Generate
button, or any loading/error/no-schedule state; that is Phase 3B.3.
Vitest + React Testing Library (jsdom environment) are configured and
passing: 15 tests across `App.test.tsx`, `config/appConfig.test.ts`,
and `api/client.test.ts`. `npm run build` (strict `tsc` type-check +
`vite build`) succeeds; a bounded dev-server smoke check confirmed Vite
serves the shell (HTTP 200) and was cleanly terminated afterward -- the
Vite-to-FastAPI proxy configuration exists and is committed, but that
proxy path was NOT exercised end-to-end against a live FastAPI backend
in this slice -- the smoke test only proved the Vite dev server itself
serves the app shell. No backend/solver/persistence/domain/application
code changed.

**Phase 3B.3 is CLOSED and merged to `main`** (commit `831c900`).
`frontend/src/components/`
gains `ClassSelector.tsx` (a plain labeled `<select>`, natural-ID
values, backend order preserved, no re-sort, no API call of its own)
and `TimetableGrid.tsx` (a semantic `<table>` rendering `/config`'s
day headers and the projection's period rows in exactly the order the
backend provides -- no 5x8 assumption -- with each cell's zero-or-more
entries rendered as distinct blocks, e.g. a real German/Russian split
renders as two separate blocks in one `<td>`, never merged into one
string; raw natural IDs are never shown). `App.tsx` is rewritten from
the Phase 3B.2 placeholder shell into the real orchestration: on mount,
load the pilot-fixed app config -> `GET .../config` -> select the first
backend-provided class -> `GET .../schedule/active/classes/{id}`;
changing the selector re-requests the newly selected class. Both data
effects use `AbortController`, aborting the superseded request in the
effect's own cleanup, with an additional defensive
`if (controller.signal.aborted) return;` guard on the success path of
both effects (not just the rejection path) so a stale response -- one
whose abort a mock doesn't naturally honor the way a real `fetch`
does -- can never overwrite a newer selection's state. Nine distinct UI
states are modeled via small discriminated unions (frontend config
error, config loading/error/ready, zero-class-sections, timetable
loading/loaded/no-schedule/error); the backend's own
`"Active schedule not found"` detail renders a neutral no-schedule
message rather than the generic error state, and no Generate button
exists anywhere in this slice. `src/api/client.ts` gained a small
additive, backward-compatible change: `getSchedulingConfigIndex`/
`getClassTimetable`/the internal `getJson` now accept an optional
trailing `AbortSignal`, forwarded to `fetch` only when supplied (never
`fetch(path, undefined)`, which would have broken existing exact-arity
mock assertions) -- all 7 pre-existing client tests still pass
unchanged, plus 2 new signal-forwarding tests. `src/test/setup.ts` now
explicitly registers RTL's `cleanup()` in a global `afterEach` (this
project never enables Vitest's `globals` option, so RTL's own
auto-cleanup detection never fired on its own -- a real cross-test
DOM-leak bug this slice found and fixed, benefiting every test file,
not just the new ones). `src/index.css` gained the minimum functional
rules the new table/selector/messages need (overflow wrapper,
border-collapse, minimum cell width, stacked-entry separation, message
styling) -- no Phase 3B.4 visual hardening. `App.test.tsx` was rewritten
for the new behavior (14 scenarios: loading/ready/error config states,
default-class selection, request-per-class-change, the stale-request
race guarantee, the timetable loading/no-schedule/error states, the
zero-classes state, and safe verbatim rendering of known backend
details). Frontend gate: `npm test` -- 41 passed (5 files); `npm run
build` (strict `tsc` + `vite build`) succeeds cleanly (no unhandled
React async-update warnings in either run); `dist/` removed afterward.
No backend/solver/persistence/domain/application code changed in this
slice; the full backend regression gate was re-confirmed passing
unmodified: `pytest tests_web` 100 passed, `pytest tests -m "not slow"`
119 passed/5 deselected, `pytest tests -m slow` 5 passed, `alembic
current` at `4681f7a362bd (head)` with no drift.

This slice also used the Phase 3B.3 owner-authorized one-off exception
to prove the result against real data end-to-end, entirely outside
production code: the previously-empty local dev PostgreSQL database was
bootstrapped, via a throwaway, uncommitted script, using the existing
test-only aggregate writer (`tests_web/support/problem_writer.py`)
against `fixtures/valid_fixture.py` -- this writes config only
(`School` `synthetic-school`, `AcademicYear` `ay-2026`, class sections
`8a`/`8b`/`9a`/`9b`, etc.), never a `Schedule`. Read-only inspection
confirmed the config rows existed and no `Schedule`/`ScheduleVersion`
existed yet. The schedule itself was then created exclusively through
the real product path -- a temporarily-run local FastAPI process,
`POST /schools/synthetic-school/years/ay-2026/schedule/generate` with
no body, returning `201` (first generation, `solver_status: OPTIMAL`,
`is_active: true`) -- followed by `GET .../schedule/active` and
`GET .../schedule/active/classes/8a`, both `200` with real persisted
entries. With a temporarily-run local Vite dev server and
`frontend/.env.local` (gitignored, pointing at the real
`synthetic-school`/`ay-2026` IDs) in place, both endpoints were
re-verified through the Vite proxy (`http://127.0.0.1:5173/...`),
confirming the full real chain up to the proxy boundary: persisted
`ScheduleVersion` -> real class-projection endpoint -> Vite proxy. The
real German/Russian split for class `8a` was located dynamically in the
live projection response (Period 3 on both Monday and Thursday, Period
8 on Tuesday) and confirmed to contain two fully distinct entries
(different `activity_name`/`teacher_name`/`participant_group_name`,
never merged) in the same cell. No browser extension was connected in
the automated-tooling environment used for this proof, so no automated
DOM-level render check was possible there -- the API/proxy chain was
proven automatically; the actual browser render was subsequently
confirmed by manual review (recorded below). Both bounded processes
were cleanly terminated afterward at that stage; the bootstrapped
dev-DB config and generated schedule, and `frontend/.env.local`, were
deliberately left in place locally as reusable pilot dev data/config
(neither is committed; `.env.local` is confirmed gitignored).

**Phase 3B.3 manual browser correctness review PASSED.** The product
owner manually verified the real running app in Chromium at
`http://127.0.0.1:5173/` (a temporarily-run local FastAPI + Vite dev
server pair, since terminated). Verified: the page loads; school name
and academic year are visible; the class selector is visible and
usable; class `8-B` rendered correctly; switching the selector from
`8-B` to `8-A` updated the rendered timetable; the timetable grid is
readable; no raw scheduling IDs are visible anywhere; no Generate
button exists. Most importantly, for class `8-A`, German and Russian
were visibly rendered as **two distinct stacked entries inside the same
timetable cell** -- observed at Monday/Period 3 and Thursday/Period 3,
each showing German / 8-A German / Teacher German and, separately,
Russian / 8-A Russian / Teacher Russian in that one cell. These
specific slots are solver output for this one generated version, not a
fixed scheduling invariant -- a different solve/seed could legitimately
place the same split elsewhere; only the split-rendering *behavior*
(never merged, always two distinct blocks) is the locked contract.

The following are recorded as deferred, non-blocking **Phase 3B.4**
UX/readability polish items, not Phase 3B.3 correctness defects:
duplicated school/year presentation (shown in both the config subtitle
and the loaded-timetable meta block); noisy repeated full-class labels
such as "All of 8-A"; the timetable could use more of a typical laptop
viewport's width/density; visual hierarchy/typography remain basic;
parallel-cell visual polish can improve further.

**Phase 3B.4 is CLOSED and merged to `main`** (commit `7c80eba`).
Owner decision for this slice:
`participant_group_scope` (or any other backend/API contract change)
was explicitly **rejected** -- the current domain has no authoritative
"whole class" group role (a plain, non-split `ParticipantGroup` scoped
to one `ClassSection` is structurally indistinguishable from a
split-branch group by membership shape alone; only
`TeachingRequirement.split_group_id` distinguishes a split branch, and
even that doesn't assert "this other group IS the whole class"), so
fully hiding labels like "All of 8-A" is **deferred** to a future
milestone rather than solved with a frontend string/name heuristic (see
the design-reconnaissance turn preceding this one). This slice instead:
removes the duplicated school/year presentation (`App.tsx`'s loaded-
timetable meta line now shows only `class_section_name`/
`version_number`; school name and academic year label render exactly
once, in the existing config-level subtitle); widens `.app-shell` from
`960px` to `1280px` max-width to use far more of a typical 1366px
laptop viewport; gives `TimetableGrid.tsx` a dynamically-generated
`<colgroup>` (one narrow period column, one column per `timetable.days`
entry -- never a hard-coded count) so the period column stays compact
while day columns share the remaining width; retains
`participant_group_name` and `teacher_name` exactly as before (no
group-name-based, `split_group_id`-based, or class-count-based
suppression logic added to React) but visually de-emphasizes them
(smaller, muted secondary text) so ordinary full-class labels create
less visual noise while split/merged group information stays fully
visible; adds a small "Reserved" secondary label, driven solely by the
existing `entry.source === "RESERVED_BLOCK"` field, next to the
activity name for reserved-block entries (never inferring a specific
kind like "Club" beyond what `source` actually guarantees); tightens
cell padding/line-height and lightens grid borders while keeping a
stronger header/body separation; adds `select:focus-visible` styling;
increases parallel-entry spacing slightly for clearer visual
separation between stacked split entries. Two follow-up correction
rounds, driven by real manual browser review, refined this further:
(1) width/typography -- `.app-shell` horizontal padding tightened from
`1.5rem` to `1rem` (max-width stays `1280px`; this was found, via a
headless-Chromium/CDP measurement at a true 1366x768 viewport, to be
the actual lever, not a layout bug -- the table was already exactly
filling its container) so the table reaches `~1248px` (91% of a
1366px viewport); entry activity text back to `1em`, secondary
group/teacher text up to `0.88em`, and the Reserved marker made bolder
and upright instead of italic, all while introducing no new color
semantics; (2) vertical density -- `TimetableEntryBlock` now renders
`participant_group_name` and `teacher_name` on one shared
`.timetable-entry-secondary` line (joined by `·` only when both are
present; either shown alone when only one exists; no secondary line
when neither exists) instead of two separate stacked lines, cutting
ordinary-entry height from three lines to at most two -- no data
hidden, no new heuristic, purely a layout change. No backend, API,
persistence, or migration file changed at any point; no new npm
dependency; no `ClassSelector.tsx` change (its existing native
label/select associations already met the accessibility bar). Frontend
gate (final): `npm test` -- **47 passed** (5 files: the original 41,
plus 2 from this slice's first round, plus 4 from the density round --
group+teacher combined, group-only, teacher-only, neither); `npm run
build` succeeds cleanly; `dist/` removed afterward. Backend regression
re-confirmed unmodified throughout: `pytest tests_web` 100 passed,
`pytest tests -m "not slow"` 119 passed/5 deselected, `pytest tests -m
slow` 5 passed, `alembic current` at `4681f7a362bd (head)` with no
drift. **Manual browser review at `http://127.0.0.1:5173/` (1366x768)
PASSED** across all three rounds: horizontal layout/proportions,
width/typography balance, and final vertical density -- including
explicit confirmation that German/Russian remain two clearly
independent stacked entries in the same cell, the Reserved marker on
Chess Club/Robotics reads cleanly, and no correctness regression was
observed. The repeated full-class label (e.g. "All of 8-A") remains
intentionally visible, per the owner decision above, and was
explicitly accepted as not a 3B.4 blocker.

A future **config/admin milestone** (explicitly not Phase 3B.4, not
scoped here, no timetable-page workload editor) should allow editing:
`Teacher -> Class/ParticipantGroup -> Subject/Activity -> Weekly
periods`, backed by the existing, already-persisted
`TeachingRequirement.weekly_periods` field (no new domain concept
needed for the data itself -- only a future write path + UI). That
future UI should also surface each teacher's total assigned weekly
periods/workload, derived from summing their `TeachingRequirement`s.
This has since been formally approved as the first Phase 3C admin MVP
-- see below and `DECISIONS.md` #33-#35.

**Phase 3C -- scheduling configuration / admin input: design locked
(`DECISIONS.md` #33-#35); Phase 3C.1 is CLOSED and merged to `main`
(commit `8b5b606`); 3C.2 onward NOT started.** A dedicated
reconnaissance (read-only; zero files changed) established that all 17
configuration tables are already fully readable in production via
`GET /config` but have **zero** production write access -- the only
writer of any configuration table today is the TEST-ONLY aggregate
writer (`tests_web/support/problem_writer.py`), explicitly documented
as never the template for a production write path (`DECISIONS.md`
#28). Three owner decisions now lock the first slice's direction:

- **#33** -- `ParticipantGroup` gains a mandatory, never-inferred
  `role` field (`WHOLE_CLASS`/`SUBGROUP`/`MERGED_CLASSES`), closing the
  Phase 3B.4-deferred "All of 8-A" ambiguity authoritatively in the
  domain -- exactly one `WHOLE_CLASS` group per `ClassSection` per
  `AcademicYear`, enforced by `validation/preflight.py` (implemented in
  3C.1, below; a denormalized-column/partial-unique-index DB mechanism
  was evaluated and deferred as premature while `ParticipantGroup`
  remains read-only), never by role inference from names/patterns/
  `split_group_id`/class-section count alone.
- **#34** -- the first admin MVP is **Teaching Assignments/Workload**:
  create/edit/delete a plain `Teacher -> WHOLE_CLASS ParticipantGroup ->
  Activity -> weekly_periods` `TeachingRequirement`, plus an assigned-
  workload summary (`SUM(weekly_periods) GROUP BY teacher_id`, verified
  safe to derive with no double-counting from splits/merges/block
  shape). `SUBGROUP`/`MERGED_CLASSES` assignment workflows, block/
  distribution/time-preference/resource/availability editing, and
  teacher contractual/target workload are all explicitly deferred past
  this slice -- contractual workload in particular does not exist in
  the domain today and is recorded only as a future capability, not
  designed now.
- **#35** -- configuration becomes write-locked the moment an
  `AcademicYear` has a generated `Schedule` (reusing the existing
  `ScheduleVersionRepository.get_active_schedule` check that already
  gates a second `Generate` call), to prevent the persisted
  configuration from silently drifting away from what a historical
  `ScheduleVersion`'s denormalized joins display (a known, accepted
  Phase 3A3 MVP limitation, `DECISIONS.md` #31). No stale-schedule
  state, no auto-invalidation, no config snapshotting, and no
  regenerate lifecycle are built in this MVP -- only the one write-gate
  check the future lifecycle will eventually build on.

**Phase 3C.1 (`ParticipantGroup` role domain/persistence contract) is
CLOSED -- implemented, reviewed, and merged to `main` at commit
`8b5b606`.**
`ParticipantGroupRole` (`WHOLE_CLASS`/`SUBGROUP`/`MERGED_CLASSES`,
`domain/groups.py`) is the single authoritative source of a group's
role -- a plain `str, Enum` matching the codebase's existing
convention, with **no default value anywhere**, so every construction
site must specify it explicitly. `validation/preflight.py` gained the
new `_check_participant_group_roles` rule: `WHOLE_CLASS`/`SUBGROUP`
groups must have exactly 1 `class_sections` member, `MERGED_CLASSES`
must have 2 or more, and every `ClassSection` must have exactly one
canonical `WHOLE_CLASS` group -- these invariants are owned solely by
preflight, never by `ParticipantGroup` itself (which stays a plain
frozen data holder, no `__post_init__`) and never by a database
structure beyond a row-local `CHECK` on the three valid values.
`persistence/models.py`'s `ParticipantGroup.role` is `TEXT NOT NULL`
with `ck_participant_group_role`, added via migration `01b2ae564170`
with **no `server_default` and no backfill** -- deliberately
fail-closed, so it only succeeds against a `participant_group` table
with zero existing rows. `persistence/mappers.py`, `api/schemas.py`
(`ParticipantGroupResponse.role: str`), and `api/serializer.py` were
updated additively; `GET /config` now exposes `role` for every
participant group, purely additive (the current frontend's
`SchedulingConfigIndexResponse` type doesn't even mirror
`participant_groups` today, so this is invisible to it -- confirmed no
frontend file needed to change). `fixtures/valid_fixture.py`,
`fixtures/impossible_fixture.py`, `fixtures/school_scale/curriculum.py`,
and `tests_web/support/problem_writer.py` were updated to set `role`
explicitly per each fixture's own authorial intent -- never a
name/pattern heuristic (`valid_fixture.py`'s 7 groups: `pg_8a`/
`pg_8b`/`pg_9a`/`pg_9b` -> `WHOLE_CLASS`; `pg_8a_german`/
`pg_8a_russian` -> `SUBGROUP`; `pg_9a_9b_merged` -> `MERGED_CLASSES`).
No `ParticipantGroup` write path/CRUD exists yet -- it remains
read-only reference data, exactly as Decision #34 scopes the first
admin MVP. Local dev-DB procedure: the synthetic dev database was
backed up (`pg_dump`, outside the repo) before any change; its pilot
config and generated schedule were then cleared (cascading delete from
the `School` row, per Decision #26's aggregate-oriented CASCADE rule);
the fail-closed migration was applied cleanly to the now-empty table
(both the dev and the separate test database); the dev database was
re-seeded from the updated, role-aware `valid_fixture.py` through the
same TEST-ONLY writer used since Phase 3B.3; and a real schedule was
then regenerated through the production `POST .../schedule/generate`
path (`201`, `OPTIMAL`, 160 entries) -- the live `/config` response was
confirmed to expose the correct `role` for all 4 `WHOLE_CLASS` + 2
`SUBGROUP` + 1 `MERGED_CLASSES` groups. Role cardinality is evaluated
against *distinct* `ClassSection` membership, never raw tuple length: a
directly-constructed `SchedulingProblem` (preflight is callable
independently of persistence) can still contain a duplicated
`class_sections` entry such as `("c1", "c1")`, which must never let
`MERGED_CLASSES` appear structurally valid merely because the tuple
happens to have length 2 -- `_check_participant_group_roles` rejects
any such duplicate on its own (`PARTICIPANT_GROUP_DUPLICATE_CLASS_SECTION`),
independent of and in addition to the per-role cardinality check;
persistence already independently prevents this via
`participant_group_class_section`'s existing `UNIQUE` constraint, kept
as real defense-in-depth. Test gate: `pytest tests_web` 100 passed;
`pytest tests -m "not slow"` **132 passed/5 deselected** (the 119
pre-Phase-3C.1 baseline + 13 new preflight tests in
`tests/test_preflight.py`, which itself grew from 11 to 24 tests --
role-cardinality mismatches for all three roles at both 2+ and 0
members, the canonical-WHOLE_CLASS-per-ClassSection checks, a valid
mixed-role case, the duplicate-membership checks above, and a small
dedicated `ParticipantGroupRole` enum-value test); `pytest tests -m
slow` 5 passed; `alembic current` at `01b2ae564170 (head)` with no
drift; `npm test` 47 passed, `npm run build` succeeds (frontend
genuinely untouched). **Phase 3C.1 is implemented, reviewed, committed
(`8b5b606` "feat: add participant group roles"), and merged to `main`
-- not pushed.**

**Phase 3C.2a (teaching-assignment application/persistence backend +
generation-vs-config-write concurrency correctness, `DECISIONS.md` #36)
is IMPLEMENTED, REVIEWED, COMMITTED (`2f4f9e6` "feat: add teaching
assignment write backend"), and MERGED to `main` -- not pushed. Phase
3C.2a is CLOSED.** New `TeachingAssignmentService` (`application/teaching_assignment_service.py`)
is the narrow write use case for create/update/delete of a **plain**
`WHOLE_CLASS` `TeachingRequirement` -- "plain" (Decision #34's editable
predicate, corrected and finalized here) means: its `participant_group`
resolves to role `WHOLE_CLASS`; `split_group_id is None`; `block_policy`
is the default `FLEXIBLE` with no explicit `block_sizes`;
`distribution_policy` is the domain default; `time_preferences == ()`;
`resource_requirement is None`; and **zero** `FixedPlacement` objects
reference it (checked separately, since `FixedPlacement` is a distinct
domain object, not a `TeachingRequirement` field). Any one violation
makes a requirement advanced/read-only for this service; update/delete
reject it outright rather than normalizing or stripping the advanced
feature. Natural IDs are backend-generated, opaque, and never truncated
(`f"req_{uuid4().hex}"`, a full 32-hex-character UUID4, via an
injectable `id_factory` for deterministic tests) -- never a client-
supplied ID, never a DB counter. The dedicated
`TeachingAssignmentRepository` port (not generic CRUD) and its
`SqlAlchemyTeachingAssignmentRepository` adapter serialize every
configuration write through a `SELECT ... FOR UPDATE` on the target
`AcademicYear` row (acquired in a short, ordinary transaction), reload
the authoritative current configuration under that lock, and re-run the
same pure validation used for the earlier fast-fail check
(`application/teaching_assignment_rules.py`) authoritatively against
that fresh reload before writing -- this is the same lock
`GenerateScheduleService`'s final persist step now also acquires (see
below), so the two write paths can never race each other, and it closes
the pre-existing concurrent-duplicate-create race too, with no new
database `UNIQUE` constraint. `GenerateScheduleService` still opens no
DB session/transaction across CP-SAT solving (Owner Decision 4/#31
unchanged); only immediately before its final persist does it open a
transaction, lock the same `AcademicYear` row, reload the current
configuration, and compare it (by the `SchedulingProblem` dataclass's
own structural equality) against the exact configuration the solver
used -- a mismatch aborts with zero rows persisted and a new, retryable
`ConfigurationChangedDuringGenerationError`; a match re-checks the
Decision #35 schedule-exists gate under the same lock before persisting
and committing. Save-time validation only blocks a write on a *newly
introduced* preflight error (diffing preflight against the pre-write
baseline); `TEACHER_OVERLOADED` and `CLASS_OCCUPANCY_MISMATCH` are
treated as non-blocking warnings returned to the caller, so an admin's
ordinary mid-configuration incompleteness never blocks an otherwise-
valid save. **No schema change was needed or made -- Alembic head is
still `01b2ae564170`, unchanged.** This slice implements no HTTP write
routes, no read/workload projection, and no frontend/React Router
changes -- those remain **Phase 3C.2b, not started.** Test gate for
3C.2a: 27 new pure application-level tests
(`tests/test_teaching_assignment_service.py`, no DB) plus 8 new
real-PostgreSQL persistence/concurrency tests
(`tests_web/test_teaching_assignment_repository.py`, including a
genuine `threading.Barrier`-synchronized concurrent-duplicate-create
proof and both orderings of the generation-vs-write race) all pass;
full regression confirmed green (`pytest tests_web` 108 passed;
`pytest tests -m "not slow"` 159 passed/5 deselected; `pytest tests -m
slow` 5 passed; `npm test` 47 passed; `npm run build` succeeds;
`alembic current`/`alembic check` still `01b2ae564170 (head)`, no
drift).

**Phase 3C.2b (Teaching Assignments HTTP API + read/workload
projection, `DECISIONS.md` #34-#36) is IMPLEMENTED, REVIEWED,
COMMITTED, and MERGED to `main` at commit `9570358` -- Phase 3C.2b
CLOSED.** A preceding read-only
technical contract gate found zero genuine owner decisions remaining --
every open question resolved from the already-locked ADRs and existing
house style. A new, dedicated, read-only
`TeachingAssignmentsProjectionService` (`application/`) -- kept
separate from the write-only `TeachingAssignmentService`, mirroring the
existing `ClassTimetableService`/`GenerateScheduleService` split --
builds the `GET .../teaching-assignments` page projection from one
`SchedulingProblemRepository.load_by_school_and_year` call plus
`ScheduleVersionRepository.get_active_schedule` for
`configuration_locked`; no new persistence read port was needed.
Editability (`editable`/`advanced_reasons`) is never reimplemented --
every requirement is passed through the existing
`teaching_assignment_rules.plain_reasons` predicate verbatim, so a
`FixedPlacement` reference makes an otherwise-plain requirement
non-editable automatically. The projection returns **every**
`TeachingRequirement` (plain and advanced alike, never just the
editable subset), backend-owned `teachers`/`activities` reference-data
lists, and the authoritative `whole_class_targets` mapping from each
`ClassSection` to its canonical `WHOLE_CLASS` `ParticipantGroup` --
built strictly from `role`/`class_sections`, never a name or
class-count heuristic, and a `ClassSection` whose Decision #33
invariant is broken is simply omitted (fail-soft; this projection is
not that invariant's enforcement point) rather than guessed. Every list
preserves the existing ordinal-ordered tuple order already produced by
`SchedulingProblemRepository` -- no new sorting introduced anywhere.
`teacher_workloads` sums **every** requirement type per teacher
(plain, advanced, `WHOLE_CLASS`/`SUBGROUP`/`MERGED_CLASSES` alike),
including teachers with zero requirements at `total_weekly_periods: 0`.

Four routes were added in a new `api/teaching_assignment_routes.py`:
`GET`/`POST /schools/{school_id}/years/{year_id}/teaching-assignments`
and `PUT`/`DELETE .../teaching-assignments/{requirement_id}` -- no
`PATCH` (the write semantics were always full-replacement), no separate
workload endpoint. `POST`/`PUT` return only `{"id", "warnings"}` (201/
200); `DELETE` returns `{"deleted_id", "warnings"}` at 200, never 204,
since a delete can legitimately surface a non-blocking warning a
bodyless response would silently discard -- none of the three reload
and return the full saved-row projection, since the caller must
re-fetch the unified `GET` afterward anyway (workload totals, ordering,
and lock state may all have changed). `warnings` reuses the existing
`ValidationDiagnosticResponse` shape verbatim. Error mapping:
`SchedulingProblemNotFoundError`/`TeachingAssignmentNotFoundError` ->
code-less 404s (matching the existing house style exactly);
`UnknownReferenceError`/`NonWholeClassTargetError`/
`InvalidTeachingAssignmentError` -> 422 with a stable `code`;
`AdvancedRequirementNotEditableError`/`DuplicateTeachingAssignmentError`/
`ConfigurationLockedError` -> 409 with a stable `code` --
`SCHEDULING_CONFIGURATION_LOCKED` is now the locked Decision #35 HTTP
contract. Separately, the existing `POST .../schedule/generate` route
gained one new mapping: `ConfigurationChangedDuringGenerationError`
(Decision #36) was previously uncaught there and would have leaked as a
generic 500 the first time it could actually occur in production; it
now maps to 409 `CONFIGURATION_CHANGED_DURING_GENERATION`, reusing the
existing `GenerationErrorResponse` shape, with no other generate error
semantics changed.

No persistence schema change was needed or made -- Alembic head is
still `01b2ae564170`, unchanged, no drift. Test gate for 3C.2b: 21 new
pure application-level tests
(`tests/test_teaching_assignments_projection_service.py`, no DB) plus
28 new real-PostgreSQL HTTP integration tests
(`tests_web/test_teaching_assignment_api.py`, covering every GET/POST/
PUT/DELETE contract path plus the generate-route regression) all pass;
full regression confirmed green (`pytest tests_web` 136 passed;
`pytest tests -m "not slow"` 180 passed/5 deselected; `pytest tests -m
slow` 5 passed; `npm test` 47 passed; `npm run build` succeeds;
`alembic current`/`alembic check` still `01b2ae564170 (head)`, no
drift). No frontend/React Router changes -- the frontend remains
genuinely untouched. **Phase 3C.2b is CLOSED.** With 3C.2a (application/
persistence write backend, concurrency correctness) and 3C.2b (HTTP
API, read/workload projection) both CLOSED, **Phase 3C.2 -- the
Teaching Assignments backend/API milestone -- is complete.**

**Phase 3C.3a (frontend routing + shared application shell + read-only
Teaching Assignments page) is IMPLEMENTED, REVIEWED (manually
browser-reviewed by the product owner against a real running
backend/frontend, both the `/timetable` and
`/configuration/teaching-assignments` routes), COMMITTED, and MERGED
to `main` at commit `1499377` -- 3C.3a CLOSED.** `react-router-dom`
(`^7.18.3`) was added -- the frontend's only new dependency -- now that
the product has a second real page; `App.tsx` is now only the router
root (`BrowserRouter`/`Routes`), `/` redirects to `/timetable`, and an
explicit `*` not-found route replaces any silent fallback. The
pre-existing timetable experience moved to `pages/TimetablePage.tsx`
under `/timetable` with no behavior change (same component logic, same
tests, only the import paths/file location changed); a new
`pages/TeachingAssignmentsPage.tsx` is live at
`/configuration/teaching-assignments`. Both pages render inside a new
shared `components/AppShell.tsx` top-navigation layout (product name
strongest, a single shared "school · academic year" context line
secondary/muted, a two-item nav with `NavLink` restrained-blue-underline
active-state styling, a `<main>` content region via `Outlet`) -- a
compact top bar, not a sidebar, with no placeholder nav items for
unbuilt future sections (School Setup, Constraints, ...) and no
Redux/Zustand/query-library state management. `AppShell` owns the one
visible school/year context line, sourced from `config/appConfig.ts` +
its own `getSchedulingConfigIndex` read; `TimetablePage` deliberately no
longer renders its own duplicate copy of that text (it still reads
`/config` independently for its own class-selector/loading/error
states, an accepted two-reads-of-a-small-endpoint tradeoff documented
in `AppShell.tsx`, not a shared client-side cache).

The Teaching Assignments page is **read-only in 3C.3a**: it loads
`GET .../teaching-assignments` via a new, dedicated
`api/teachingAssignments.ts` module (mirroring `api/client.ts`'s
existing relative-URL/`ApiError` discipline, reusing its exported
`getJson` helper rather than duplicating fetch/error handling) and
renders the Phase 3C.2b projection directly -- never reconstructed from
`/config`. Following manual browser review, a visual-correction pass
(no data/contract change) reworked the page's layout: a compact,
responsive two-column "Teacher workload" row grid (every teacher,
including zero-period ones, the raw backend total verbatim -- no
cards/shadows/progress bars/invented target-remaining-percentage,
collapsing to one column at narrow widths) replaced the original
narrow single-column table; the assignments table's columns are
explicitly width-balanced (`Teacher | Class/Group | Activity | Weekly
periods | Configuration`, `Configuration` renamed from the original
`Type` heading, no `Actions` column yet); a neutral "Advanced" badge
shows backend `advanced_reasons` codes as separate friendly-labeled
reason chips (`ADVANCED_REASON_LABELS`, unrecognized future codes fall
back to their raw form) rather than one comma-joined sentence;
`SUBGROUP`/`MERGED_CLASSES` participant-group-role badges are styled
visually quieter than the "Advanced" status badge (target semantics,
not a warning; `WHOLE_CLASS` shows no badge); and a `configuration_locked`
banner renders as a restrained, informational (not error-styled) notice
when true -- advanced rows stay fully visible throughout, never hidden
or implied broken. No create/edit/delete UI exists yet -- not even
disabled controls -- since that belongs to Phase 3C.3b. CSS for both the
shared shell and this page uses page-/component-scoped class selectors
(`.assignments-page .page-section`, etc.), never bare element selectors
like `section`/`section h2`, so a future page's own sections/headings
can't inherit this page's rules. No backend/schema change was needed or
made; Alembic head is still `01b2ae564170`, unchanged, no drift.

Verification baseline at closure: frontend test gate **76 tests
passing**, `npm run build` clean; backend regression reconfirmed
unaffected (`pytest tests_web` **136 passed**; `pytest tests -m "not
slow"` **180 passed/5 deselected**); `alembic current`/`alembic check`
still `01b2ae564170 (head)`, no drift. One minor future polish note:
"Non-whole-class target" (the friendly label for the
`participant_group_role` advanced reason) remains somewhat technical
presentation copy and may be renamed in a later UX polish pass -- not
changed during this closure.

**Phase 3C.3b (create/edit/delete interaction, warnings/error UX
polish) is IMPLEMENTED, REVIEWED, COMMITTED (`f608b7d` "feat: add
teaching assignment mutations"), and MERGED to `main` -- not pushed.
Phase 3C.3b is CLOSED.** Frontend-only, consuming
the already-merged 3C.2a/3C.2b write/read backend contract
(`docs/DECISIONS.md` #34-#36) exactly as designed -- **no backend or
schema change**, Alembic head unchanged at `01b2ae564170`, no drift.

Create/edit/delete now exists, but only for plain, editable
`WHOLE_CLASS` assignments -- advanced rows (`editable: false`) stay
conceptually read-only for their own reason and never gain mutation
controls; a global `configuration_locked` separately disables (but
still shows) Edit/Delete on plain rows, with the existing lock banner
as the one shared explanation -- the two disabled-looking states are
deliberately never visually or semantically conflated (`ActionsCell`,
`pages/TeachingAssignmentsPage.tsx`). Add/Edit use a new right-side
modal drawer (`pages/AssignmentDrawer.tsx`: `role="dialog"`,
`aria-modal`, focus trap, Escape/overlay-click/Close/Cancel to close,
focus returned to the triggering button on close); Delete uses inline
per-row confirmation (Confirm/Cancel), never a one-click delete.

Every successful mutation triggers one authoritative re-fetch of the
same unified `GET .../teaching-assignments` projection -- assignments/
workloads/`configuration_locked` are only ever taken from that
response, never hand-patched from a write's own `{id, warnings}`/
`{deleted_id, warnings}` body, and the page is never blanked back to
its initial loading state during that re-fetch. Non-blocking save-time
warnings (`TEACHER_OVERLOADED`/`CLASS_OCCUPANCY_MISMATCH`) render as a
dismissible, non-error informational banner, survive that re-fetch, and
are replaced/cleared by the next mutation's own warning list. If the
write itself succeeds but the follow-up re-fetch fails, the write is
never reported as failed -- the existing (possibly now-outdated)
projection stays visible, marked stale, with every mutation control
disabled until a `Retry` re-fetch succeeds. A `409
SCHEDULING_CONFIGURATION_LOCKED` returned by any write (a stale-client
race against a schedule generated elsewhere since the page loaded)
closes the initiating drawer/confirmation, shows a transient notice,
and re-fetches into the now-genuinely-locked state -- never a false
success. `api/client.ts` gained a shared `postJson`/`putJson`/
`deleteJson` write helper alongside the existing `getJson`, and
`ApiError` gained optional `code`/`body` fields (additive only,
`detail` always stays a safe string even for FastAPI's own generic
array-`detail` 422 body) so structured backend error codes
(`DUPLICATE_TEACHING_ASSIGNMENT`, `UNKNOWN_REFERENCE`,
`NON_WHOLE_CLASS_TARGET`, `ADVANCED_REQUIREMENT_NOT_EDITABLE`,
`INVALID_TEACHING_ASSIGNMENT`) can be mapped to safe inline messages
without a generic error-handling framework. State stays component-local
`useState` throughout -- no Redux/Zustand/query library.

No unlocked dev-DB fixture was created for manual mutation review --
the canonical `synthetic-school`/`ay-2026` pilot schedule/history was
left untouched. The safest later option (not executed): a throwaway,
uncommitted local script using the existing test-only
`tests_web/support/problem_writer.py` mechanism to write a second,
schedule-free dev-only school/year, the same mechanism already used
once to seed the current pilot data.

Frontend test gate: 133 tests passing (the pre-existing 76 plus 57 new
-- API-client/module coverage plus full Add/Edit/Delete page coverage,
including accessibility and the stale-projection-safety scenarios);
`npm run build` succeeds. Backend regression reconfirmed unaffected
(`pytest tests_web` 136 passed; `pytest tests -m "not slow"` 180
passed/5 deselected). Alembic still `01b2ae564170 (head)`, no drift.

A follow-up visual-polish pass (no mutation-semantics change) fixed a
real header-collision readability defect the manual review caught:
"WEEKLY PERIODS"/"CONFIGURATION" ran into each other because the
shared `.numeric-cell` rule carried `white-space: nowrap` onto the
header too -- scoped to the data cell only now, so the longer header
label wraps within its own column instead. Column widths were
rebalanced to sum to exactly 100% (Teacher 14% / Class-Group 20% /
Activity 17% / Weekly periods 11% / Configuration 26% / Actions 12%,
previously summing past 100%). Row Edit/Delete buttons gained a
`.action-button-delete` variant -- Edit stays the existing restrained
neutral style, Delete is a quiet destructive affordance (muted rose,
not bright red, by default) that only reads clearly destructive on
hover/focus; "Add assignment" remains the page's one strong primary
action throughout. The inline delete-confirmation's `min-width` was
removed (it could force it wider than its fixed-layout Actions column)
so it now always stays contained inside its own cell. All 133 tests
remained green; no test changes were needed.

**Manual browser review (product owner) PASSED**, performed against a
local-only, second, deliberately unlocked dev dataset
(`synthetic-review-school`/`ay-review-2026`, seeded via the existing
TEST-ONLY `tests_web/support/problem_writer.py` mechanism against the
same `fixtures/valid_fixture.py` shape as the canonical pilot --
**not** a production seeding endpoint, never committed) so the
Add/Edit/Delete UI could be exercised unlocked without touching the
canonical, genuinely-locked `synthetic-school`/`ay-2026` pilot
schedule/history, which was confirmed untouched throughout. Confirmed:
Add assignment enabled unlocked; advanced rows stayed Read-only; plain
rows exposed Edit/Delete; the Add drawer opened correctly and the Edit
drawer prepopulated correctly; a real create (Teacher German -> 9-A ->
German) and a real edit (weekly periods -> 2) both saved successfully,
with the authoritative post-write refresh showing the updated Teacher
German workload; a duplicate-create attempt correctly showed the
inline `"This teacher already has an assignment for this class and
activity."` message; Delete required an explicit "Confirm delete"
click (never one-click) and the confirmed delete removed the row; the
header-collision fix and restrained Delete styling were both accepted.

**With 3C.3a and 3C.3b both CLOSED, Phase 3C.3 (Teaching Assignments
frontend milestone) is complete.**

**Next product slice: Minimal Schedule Generation Trigger UI** (no new
phase number -- this sits outside 3C.1-3C.5's own locked decision set;
see the read-only gate record above/below for the full trade-off
analysis). The product owner chose this before continuing into the
broader 3C.5 reference-data-CRUD direction: it closes the one real gap
in the school-configuration -> generation -> timetable-review pipeline
-- until now, `POST .../schedule/generate` (fully built and tested
since Phase 3A3) was reachable only outside the browser. **IMPLEMENTED,
REVIEWED (manually browser-reviewed by the product owner), COMMITTED,
and MERGED to `main` at commit `a3bbcb8` -- CLOSED.** Frontend-only,
zero backend/schema changes (the already-locked Decision #31 contract
is reused exactly as merged); Alembic head unchanged at
`01b2ae564170`, no drift.

The trigger lives entirely inside `TimetablePage`'s existing
"no schedule has been generated yet" empty state -- one primary
"Generate schedule" button (`.btn-primary`, the same primary-action
language "Add assignment" already established), visible only when no
active schedule exists yet; absent once a timetable is loaded, in the
no-classes state, and in the config-error state. No confirmation
dialog (initial generation is non-destructive; the backend already
rejects a duplicate). On click: the existing, already-merged, no-
request-body `POST .../schedule/generate` (`generateSchedule` in
`api/client.ts`, reusing the shared `postJson` -- `body` passed as
`undefined` exactly like `deleteJson` already does, so no
`Content-Type` header and no `{}` body are ever sent) -- the button
disables and its label becomes "Generating…" while in flight, with no
fake progress percentage/bar and no polling (a single synchronous
request/response, matching CP-SAT's actual ~1-2s solve time at this
pilot's scale). On success, a small `generationRefreshToken` counter
(mirroring `TeachingAssignmentsPage`'s existing `retryToken` pattern)
is bumped and added to the *already-existing* per-class
`getClassTimetable` fetch effect's dependency list -- the exact same
authoritative GET this page already performs re-runs and the grid
renders; there is no second, hand-built display path from
`GenerateScheduleResponse`'s own body, no manual reload, no navigation
away from `/timetable`. `TeachingAssignmentsPage` becomes
`configuration_locked: true` purely from its own next `GET` -- zero
coupling was introduced between the two pages.

A stale-browser `409 SCHEDULE_ALREADY_EXISTS` is deliberately **not**
shown as an error: it triggers the identical success-path refetch (the
schedule the browser didn't know about yet simply renders), mirroring
3C.3b's `SCHEDULING_CONFIGURATION_LOCKED` stale-lock-race handling
exactly. `409 CONFIGURATION_CHANGED_DURING_GENERATION` shows an inline
retryable message ("Scheduling configuration changed during
generation. Please try again.") and re-enables the button -- never a
false success. `422 INVALID_CONFIGURATION` renders its
`error.body.errors` diagnostic messages inline (reusing the exact
structured-diagnostic pattern from 3C.3b's `INVALID_TEACHING_ASSIGNMENT`
handling; an unrecognized/malformed diagnostic shape is filtered out
rather than rendered or thrown -- fails safe, never raw JSON). `409
SCHEDULE_INFEASIBLE` and a plain `404 Scheduling configuration not
found` both show their own safe backend `detail` string inline. Any
other/network failure falls back to the existing generic "Something
went wrong. Please try again." message. Every failure path re-enables
the button for another attempt.

Explicitly out of scope for this slice, unchanged: regeneration/
reoptimization, schedule history, manual schedule editing, locks UI,
a school/year selector, and all of 3C.5's broader reference-data CRUD.

Frontend test gate: 154 tests passing (the pre-existing 133 plus 21 new
-- 7 `api/client.test.ts` `generateSchedule` tests plus 14
`TimetablePage.test.tsx` tests covering visibility, the in-flight/
double-click guard, success, the stale-already-exists refresh, and
each structured failure path; one pre-existing test that had asserted
"no Generate button exists" -- accurate for its Phase 3B-era scope --
was updated to assert the button is now present and enabled, since
that exclusion is exactly what this slice deliberately supersedes);
`npm run build` succeeds. Backend regression reconfirmed unaffected
(`pytest tests_web` 136 passed; `pytest tests -m "not slow"` 180
passed/5 deselected); Alembic still `01b2ae564170 (head)`, no drift.

**Manual browser review by the product owner PASSED**, using
the local-only, unlocked `synthetic-review-school`/`ay-review-2026`
dataset. Owner-confirmed pre-generation state: no active schedule
existed, "Generate schedule" was visible, the timetable grid was
absent. The product owner clicked Generate schedule exactly once;
afterward: the generated timetable appeared automatically on the same
`/timetable` page with no manual browser reload, Version 1 rendered
correctly, and the class selector remained functional throughout.
`TeachingAssignmentsPage`, on its own next load, correctly showed
`"Assignments are read-only because a schedule has already been
generated for this configuration."`, with "Add assignment" disabled and
every editable row's Edit/Delete controls locked -- entirely from the
existing, unmodified `configuration_locked` contract, with zero
coupling code added between the two pages. **The full browser-only
Configure -> Generate -> Review workflow PASSED end-to-end for the
first time.** The canonical `synthetic-school`/`ay-2026` pilot was
reconfirmed completely untouched throughout (`configuration_locked:
true`, 25 assignments, its pre-existing version-1 schedule unchanged).
No backend/schema change was needed or made. Explicitly still future
work, not pulled into this slice: a teacher timetable view, lunch/break
visual presentation, an all-school/master timetable, manual schedule
editing, locks UI, reoptimization, the regeneration lifecycle, and
schedule history.

**Minimal Schedule Generation Trigger UI is CLOSED.**

**Teacher Timetable View** (no new phase number -- outside
3C.1-3C.5's own locked decision set, same as the schedule-generation
trigger before it). The product owner selected this ahead of 3C.5's
broader reference-data CRUD; the architecture/product gate confirmed
the same backend-projection boundary already proven for the class
timetable applies directly, with zero schema/migration/solver changes
needed. **IMPLEMENTED, REVIEWED (manually browser-reviewed by the
product owner), COMMITTED, and MERGED to `main` at commit `a8d8e75` --
CLOSED.**

Backend: a new sibling application projection,
`application/teacher_timetable_service.py::TeacherTimetableService`
(mirrors `ClassTimetableService` exactly -- same two repository ports,
same no-DB-session-held-during-projection discipline, same strict
by-ID lookup that raises `KeyError` on a malformed stored reference
rather than inventing a display label), a new `TeacherNotFoundError`
(`application/errors.py`, mirrors `ClassSectionNotFoundError`), the
`TeacherTimetableResponse` schema family (`api/schemas.py`) and its
serializer (`api/serializer.py::teacher_timetable_response_from_view`),
composed via a new `get_teacher_timetable_service` dependency
(`api/dependencies.py`, identical session-factory-backed composition),
and the sibling route `GET .../schedule/active/teachers/{teacher_id}`
(`api/schedule_routes.py`) with the exact same error-mapping style as
the class route (404 `Scheduling configuration not found`/`Active
schedule not found`/`Teacher not found`, no new structured `code`).
`TeacherTimetableEntry` additionally carries `participant_group_role`
and resolved `class_sections` (never present on `ClassTimetableEntry`,
which doesn't need them) so the frontend can reproduce the
WHOLE_CLASS/SUBGROUP/MERGED_CLASSES display rule already established
for Teaching Assignments without ever inferring role from name/count.
Covered by a pure, DB-free fake-repository suite
(`tests/test_teacher_timetable_service.py`, 14 tests, mirroring
`tests/test_class_timetable_service.py`, including a strict-lookup
`KeyError` test) and a real-PostgreSQL/real-solver integration suite
(`tests_web/test_teacher_timetable_api.py`, 11 tests, mirroring
`tests_web/test_class_timetable_api.py`, including a zero-load-teacher
case built via `dataclasses.replace` on the valid fixture). **No
schema/migration change; Alembic head unchanged at `01b2ae564170`.**

Frontend: `TimetablePage` gained a `mode: "class" | "teacher"` switch
(compact "Class | Teacher" toggle, `aria-pressed`) -- still exactly one
`/timetable` route, no new top-nav destination. Class mode is
byte-for-byte unchanged (selector, grid, Generate schedule, every
existing state). Teacher mode is a fully independent sibling: its own
`TeacherSelector` component (mirrors `ClassSelector` exactly, sourced
from `configState.config.teachers` -- the same single `/config` fetch
this page already made, now additionally mirroring `teachers` as a
narrow `TeacherSummary[]`, zero coupling to
`TeachingAssignmentsPage`'s own separate `TeacherOption`), its own
`getTeacherTimetable` fetch effect (own `AbortController`, same
stale-response race guard as class switching), and a dedicated
`TeacherTimetableGrid` component (not a generalization of
`TimetableGrid` -- the entry shapes differ enough that forcing both
through one abstraction would blur rather than clarify either one).
Both modes' data fetch independently of which is currently visible, so
switching is instant with no new network request, and both refetch
together on the same `generationRefreshToken` Generate already bumps
(a generated schedule is school/year-wide, affecting both projections
identically). Generate stays Class-mode-only, never duplicated into
Teacher mode. WHOLE_CLASS entries show the class name directly, no
badge; SUBGROUP/MERGED_CLASSES entries show the participant group's
name plus the existing quiet `.group-role-badge` (the two-entry label
mapping is duplicated locally in `TeacherTimetableGrid.tsx` rather than
extracted, per the smallest-solution guidance -- `TeachingAssignmentsPage.tsx`
was not touched). Free periods render as blank cells, never the text
"Free". A zero-teachers state shows "No teachers are configured for
this school/year yet."; a no-schedule-yet teacher state shows "No
schedule has been generated yet." (no Generate control). Lunch/break
presentation remains explicitly deferred -- `Period.block_id`/
`is_instructional` already exist and require no future schema/solver
change to surface, but neither is exposed by this slice.

Verified live (assistant, API-level) against the real running backend/
frontend on the local-only `synthetic-review-school`/`ay-review-2026`
dataset (8 teachers, its already-generated version-1 schedule from the
Schedule Generation UI's own manual review): `GET
.../schedule/active/teachers/t_german` correctly returned a SUBGROUP
target (`8-A German`), `t_history` correctly returned a MERGED_CLASSES
target (`9-A + 9-B Merged History`, both `9-A`/`9-B` resolved), and an
unknown teacher ID correctly returned `404 {"detail": "Teacher not
found"}`. The canonical `synthetic-school`/`ay-2026` pilot was
reconfirmed untouched throughout (`configuration_locked: true`, 25
assignments, its pre-existing version-1 schedule unchanged).

**Manual browser review by the product owner PASSED**, on the same
`synthetic-review-school`/`ay-review-2026` dataset: Class mode
rendered correctly; the Class | Teacher switch worked; **Teacher
Math**'s normal whole-class timetable rendered with correct
activity/class labels and free periods staying blank; **Teacher
Russian**'s SUBGROUP timetable showed the `8-A Russian` target with
the correct SUBGROUP badge; **Teacher History**'s MERGED_CLASSES
timetable was confirmed at Friday/Period 3 showing exactly `History` /
`9-A + 9-B Merged History` / `MERGED CLASSES`; switching between
teacher selections worked; the existing generated Version 1 remained
visible and correct throughout; Teacher mode showed no Generate
control; and Class mode remained fully functional with the existing
generated schedule. This completes manual proof for WHOLE_CLASS,
SUBGROUP, MERGED_CLASSES, free cells, and Class/Teacher mode
switching.

Frontend test gate: 185 tests passing (154 plus 31 new -- 5
`TeacherSelector` component tests, 7 `getTeacherTimetable` API-client
tests, and 19 new `TimetablePage` Teacher-mode tests covering mode
switching, the teacher selector, WHOLE_CLASS/SUBGROUP/MERGED_CLASSES/
free-cell/zero-load rendering, every state, and Class-mode regression);
`npm run build` succeeds. Backend regression reconfirmed unaffected
beyond the 25 new tests above (`pytest tests_web` 147 passed total;
`pytest tests -m "not slow"` 194 passed/5 deselected total); Alembic
still `01b2ae564170 (head)`, no drift.

**Teacher Timetable View is CLOSED.** Explicitly still separate future
work, none of it pulled into this slice: real-school setup/reference-
data CRUD (Teachers, Classes, Subjects/Activities), lunch/break
presentation, an all-school/master timetable, manual schedule editing,
locks UI, regeneration/reoptimization, and schedule history. Next
product slice: to be selected after Teacher Timetable View closure.

Recommended sequencing (`DECISIONS.md` #35 for full detail): **3C.1**
`ParticipantGroup` role domain/persistence contract (no UI) -> **3C.2**
teaching-assignment write backend (no frontend editor) -> **3C.3**
configuration frontend foundation (introduces a lightweight React
Router for `/timetable` + `/configuration/teaching-assignments`, since
Phase 3B's no-Router decision was conditioned on there being only one
page) -> **3C.4** manual browser UX hardening -> **3C.5** Phase 3C
closure + broader configuration roadmap. Teachers/ClassSections/
Activities/canonical `WHOLE_CLASS` groups remain pre-existing read-only
reference data for this first slice; full reference-data CRUD is real
future work, not dragged into 3C.1-3C.4. Out of scope for the whole
milestone: auth, teacher timetable view, manual schedule editing,
locks/reoptimization UI, schedule history UI, print/export, mobile
redesign, configuration versioning, stale-schedule lifecycle,
regeneration workflow, teacher contractual workload, all-entity CRUD in
one milestone, and subgroup/merged-group/advanced-policy editors.

After Phase 3A3 (3A3.1-3A3.4) closes, the roadmap continues:

**Phase 3B -- React/TypeScript first visual timetable**: a real
generated/persisted 5x8 class timetable rendered in the browser, via a
dedicated backend class-timetable projection endpoint (`DECISIONS.md`
#32) consumed by React -- not a client-side projection over
`GET .../schedule/active` (and `/config`) directly.

Roadmap: **3A2.4 -> 3A3 -> 3B (3B.1 -> 3B.2 -> 3B.3 -> 3B.4) -> 3C
(3C.1 -> 3C.2 -> 3C.3 -> 3C.4 -> 3C.5).**

**Real-School Setup MVP -- Slice A (Teacher `first_name`/`last_name`
migration): IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at
commit `4775684` "feat: split teacher name fields" -- CLOSED. Not
pushed.** Migration verified on both the dev and test databases
(upgrade backfill correct, downgrade/upgrade round trip exact); API
compatibility verified live against the running dev app; frontend
confirmed unchanged. Owner Decision #37
(`DECISIONS.md`) locks Teacher identity as `first_name`/`last_name`
instead of one `name`, both required constructor arguments (no
dataclass default), with a derived `full_name` display property.
Scope was deliberately narrow: domain `Teacher`, the `teacher` ORM
table (new staged/reversible Alembic migration `cae76cba3c58`), the
mapper, and every existing display-name call site (`GET /config`,
Teaching Assignments, Class Timetable, Teacher Timetable) updated to
derive their one public `name`/`teacher_name` string from
`teacher.full_name` -- no public API gained `first_name`/`last_name`
fields, no Teacher CRUD/write path was added, and zero frontend files
changed. Live-validated against the real dev database (both the
canonical pilot and the local review dataset, `pg_dump`-backed up
first) and the separate test database: existing teacher names resolve
unchanged through the running app's endpoints post-migration, and the
downgrade/upgrade round trip is exact; both databases left on the new
head. Test gate: `tests_web` 147/147; core `tests -m "not slow"`
199 passed/5 deselected (194 pre-existing + 5 new in the new
`tests/test_people.py`, covering `full_name` behavior and mapper
round-tripping); frontend 185/185, build clean; Alembic: exactly one
new head (`cae76cba3c58`), current == head, no drift. Explicitly not
part of Slice A, real future work: Teacher/Classes/Subjects CRUD
endpoints and write services, School Setup UI, exposing
`first_name`/`last_name` on any public API response, real-school data
entry, and auth/user integration. The broader Real-School Setup MVP is
**not** complete -- this is Slice A only.

**Real-School Setup MVP -- Slice B (reference-data write foundation +
Teacher CRUD): IMPLEMENTED, REVIEWED, live API reviewed, COMMITTED,
and MERGED to `main` at commit `6fb4bed` "feat: add teacher CRUD" --
CLOSED. Not pushed.** Adds `GET/POST
/schools/{school_id}/years/{year_id}/teachers` and `PUT/DELETE
.../teachers/{teacher_id}`, backed by `TeacherService`/
`SqlAlchemyTeacherRepository`, shaped exactly like the existing
Teaching Assignment write path (Decision #34): a fast un-locked
Decision #35 lock precheck, then an authoritative, Decision-#36-locked
recheck against a freshly-reloaded `SchedulingProblem` immediately
before committing. The `AcademicYear` resolve/lock/lock-recheck
sequence, previously private to `teaching_assignment_repository.py`,
is now shared (unchanged behavior, confirmed by its full existing test
suite staying green) via a new persistence-private module,
`persistence/configuration_write_lock.py`. Teacher natural IDs are
always server-generated (`teacher_<uuid4().hex>`), never
client-supplied; `first_name`/`last_name` are trimmed with a blank
result rejected (`422 INVALID_TEACHER`), no uniqueness, same-name
teachers explicitly allowed. Delete is rejected
(`409 TEACHER_IN_USE`) whenever the teacher is still referenced by a
current `TeachingRequirement`/`TeacherAvailability`/`ReservedBlock`
(deterministic `referenced_by` ordering), enforced at the application
layer even where the DB's own `teacher_availability` FK is `ON DELETE
CASCADE` -- never a silent cascade. Reuses the existing
`ConfigurationLockedError`/`TeacherNotFoundError` verbatim; zero new
Owner Decisions (`DECISIONS.md` #37's Slice B update covers the
technical detail). Live-validated this session against a new,
local-only, unlocked `teacher-crud-review-school`/
`ay-teacher-crud-2026` dataset (seeded via the existing TEST-ONLY
`write_scheduling_problem` writer, retained per the same convention as
`synthetic-review-school`/`ay-review-2026`; no `Schedule` generated for
it): create, read, update (name change with ID preserved), delete (an
unused teacher succeeds; a referenced teacher returns
`TEACHER_IN_USE`), and same-name-teacher coexistence all confirmed
through the running app's real HTTP API, with the new/updated teacher
immediately visible through `GET /config` and `GET
/teaching-assignments` (workload `0`) with no special synchronization
step. Both canonical datasets (`synthetic-school`/`ay-2026`,
`synthetic-review-school`/`ay-review-2026`) confirmed unchanged
throughout. Test gate: core `tests -m "not slow"` 222 passed/5
deselected (199 pre-existing + 23 new pure `tests/test_teacher_service.py`
cases); `tests_web` 179 passed (147 pre-existing + 23 new
`tests_web/test_teacher_api.py` HTTP-contract cases + 9 new
`tests_web/test_teacher_repository.py` cases, including a
deterministic proof of both Decision #36 race orderings for a Teacher
write); frontend 185/185, build clean, zero frontend production file
changes; Alembic unchanged at `cae76cba3c58`, single head, no drift.
Explicitly not part of Slice B: Teacher availability editing, Classes
CRUD, Subjects/Activities CRUD, ParticipantGroup editing, School Setup
UI, any frontend production feature, TeachingAssignment semantic
changes, solver changes, schedule editing, auth/user integration. The
broader Real-School Setup MVP is **not** complete -- this is Slice B
only. Next implementation slice per the approved setup contract:
**Slice C -- Classes CRUD + canonical `WHOLE_CLASS` lifecycle** (not
started).

**Real-School Setup MVP -- Slice C (Classes CRUD + canonical
`WHOLE_CLASS` lifecycle): IMPLEMENTED, REVIEWED, live API reviewed,
test-infrastructure correction reviewed, COMMITTED, and MERGED to
`main` at commit `460e42f` "feat: add class CRUD" -- CLOSED. Not
pushed.** Adds `GET/POST
/schools/{school_id}/years/{year_id}/classes` and `PUT/DELETE
.../classes/{class_id}`, backed by `ClassSectionService`/
`SqlAlchemyClassSectionRepository`. Create atomically produces exactly
one `ClassSection` + its owned, internal canonical `WHOLE_CLASS`
`ParticipantGroup` + the one membership linking them, reusing
`persistence/configuration_write_lock.py` entirely unchanged (the
shared `AcademicYear` lock/Decision-#35-recheck sequence already used
by Teacher and Teaching Assignment writes). Both natural IDs
(`class_<uuid4().hex>`/`group_<uuid4().hex>`) are always
server-generated; the administrator never supplies or manages the
canonical group. Duplicate `ClassSection` names within one
`AcademicYear` are rejected by exact, case-sensitive, trimmed
comparison (`409 DUPLICATE_CLASS`, application rule, no new DB
uniqueness); a blank name is rejected (`422 INVALID_CLASS`). For
classes created/renamed through this new path, the canonical group's
display name tracks the class's own name; pre-existing fixture-seeded
canonical groups keep their historical `"All of X"`-style names
untouched -- Slice C never rewrites them, and canonical-group
resolution is always by `role == WHOLE_CLASS` + exact-one-class
membership, never by name. Delete is rejected (`409 CLASS_IN_USE`,
deterministic `referenced_by`) whenever the class or its canonical
group is still referenced by a current `TeachingRequirement`,
`ReservedBlock`, `SUBGROUP` membership, or `MERGED_CLASSES` membership
-- `SUBGROUP`/`MERGED_CLASSES` groups themselves are never created,
renamed, or deleted by Class CRUD. A corrupted zero-or-duplicate
canonical-group state is never silently repaired -- it surfaces as an
internal defect, never a public error code. Reuses
`ClassSectionNotFoundError`/`ConfigurationLockedError` verbatim; zero
new Owner Decisions (`DECISIONS.md` #37's Slice C update covers the
technical detail -- Owner Decision #33 already settled the schema
question). Live-validated this session against a new, local-only,
unlocked `class-crud-review-school`/`ay-class-crud-2026` dataset
(seeded via the existing TEST-ONLY `write_scheduling_problem` writer,
retained per the same convention as the prior review datasets; no
`Schedule` generated for it): create, read, rename (identity preserved,
both display names updated atomically), duplicate rejection, case-
sensitive coexistence, unused-class delete (owned canonical group
confirmed gone from the database), and referenced-class delete
rejection (multi-kind deterministic `referenced_by`) all confirmed
through the running app's real HTTP API, with the new/renamed class
immediately visible through `GET /config` (new `ClassSection` +
exactly one `WHOLE_CLASS` `ParticipantGroup`) and `GET
/teaching-assignments` (`whole_class_targets`, authoritative
`participant_group_id`). All three prior datasets
(`synthetic-school`/`ay-2026`, `synthetic-review-school`/
`ay-review-2026`, `teacher-crud-review-school`/`ay-teacher-crud-2026`)
confirmed unchanged, including every pre-existing fixture canonical
group's historical `"All of X"` name, verbatim. Test gate: core `tests
-m "not slow"` 250 passed/5 deselected (222 pre-existing + 28 new pure
`tests/test_class_section_service.py` cases); `tests_web` 213 passed
(179 pre-existing + 23 new `tests_web/test_class_section_api.py`
HTTP-contract cases + 11 new
`tests_web/test_class_section_repository.py` cases, including
create/update/delete atomicity proofs and a deterministic proof of
both Decision #36 race orderings for a Class mutation). Slice C's
larger cumulative test count initially pushed a canonical single-
process `uv run python -m pytest -q tests_web` run past the local
Postgres `max_connections` limit, exposing a pre-existing
`tests_web/conftest.py::live_db_engine` fixture defect (it `return`ed
its `Engine` with no teardown hook at all, so pytest had no way to
dispose it, and every test's server-side connections accumulated
monotonically across the run). Corrected as a narrow test-
infrastructure fix, no production code touched: `live_db_engine` now
`yield`s its `Engine` and disposes it in a `finally` on every exit path
(test success, test failure, and the pre-existing reachability-probe
skip path alike) -- same function scope, same database URL, same
`Engine` configuration, same test-isolation semantics, unchanged.
Verified directly: `pg_stat_activity` connection count returned to its
pre-run baseline after a representative test sequence (no longer
monotonically accumulating), and the canonical single-process
`uv run python -m pytest -q tests_web` now passes all 213 tests with
zero DB-reachability skips, confirmed on two consecutive runs; frontend
185/185, build clean, zero frontend production file changes; Alembic
unchanged at `cae76cba3c58`, single head, no drift. Explicitly not part
of Slice C: Subjects/Activities CRUD, Teacher changes, generic
ParticipantGroup CRUD, a SUBGROUP/MERGED_CLASSES editor, Teacher
Availability editor, School/AcademicYear CRUD, any frontend production
feature, TeachingAssignment semantic changes, solver changes, auth/user
integration. The broader Real-School Setup MVP is **not** complete --
this is Slice C only. Next implementation slice per the approved setup
contract: **Slice D -- Subjects/Activities CRUD** (not started).

**Pre-Slice-D correction (a technical bug fix, NOT Owner Decision #38 --
that number remains unused) -- Teaching Assignment Activity-Kind
Invariant: IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at
commit `bade46c` "fix: enforce ordinary teaching assignment
activities" -- CLOSED. Not pushed.** The
Slice D design gate's own consistency check found (and a
rollback-isolated real-PostgreSQL proof confirmed) that
`TeachingAssignmentService` create/update never verified `activity_id`
was `ActivityKind.ORDINARY` -- only that it existed -- so a `CLUB`
activity (`club_chess` in the fixture) could genuinely become a
`TeachingRequirement`, and `TeachingAssignmentsProjectionService`
offered every `CLUB` activity as a selectable option. Both are now
corrected: `teaching_assignment_rules.py` gained one shared
`require_ordinary_activity` helper (reused by both the fast precheck
and the authoritative locked recheck via the existing `validate`
callback wiring -- `persistence/teaching_assignment_repository.py` is
untouched); a new `NonOrdinaryActivityTargetError` maps to `422
{"code": "NON_ORDINARY_ACTIVITY_TARGET", ...}`, mirroring
`NonWholeClassTargetError` exactly; the Teaching Assignments
`activities` option list now filters to `ORDINARY` only (identical
response-item shape, zero frontend change); `GET /config` remains
deliberately unfiltered (general configuration projection, not a
selector). `validation/preflight.py` gained a defense-in-depth check,
`NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY`, for any already-malformed
`SchedulingProblem` reaching generation by a path other than the
now-guarded write service. `ReservedBlock` intentionally untouched --
no production `ReservedBlock` write surface exists yet, so there is
nothing to have a symmetrical defect in. Subject duplicate-name
checking (Slice D's own future design) is corrected to be scoped to
`ActivityKind.ORDINARY` only, now that the projection leak that
justified the earlier "unique across all kinds" recommendation is
fixed -- zero new Owner Decision. Zero schema/migration impact, zero
solver change, zero frontend production change. Test gate: core `tests
-m "not slow"` 256 passed/5 deselected (250 pre-existing + 6 new:
2 Teaching Assignment service + 1 ordinary-still-succeeds + 2 preflight
+ 1 projection); canonical single-process `tests_web` 217 passed (213
pre-existing + 4 new), zero DB-reachability skips, confirmed on two
consecutive runs; Teaching Assignment regression (API + repository)
40/40; frontend 185/185, build clean; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Slice D Subject CRUD remains
**NOT implemented** -- this correction is scoped entirely to the
pre-existing Teaching Assignment defect it fixes.

**Real-School Setup MVP -- Slice D (Subjects / ORDINARY Activities
CRUD): IMPLEMENTED, REVIEWED, live API reviewed, COMMITTED, and
MERGED to `main` at commit `104bed6` "feat: add subject CRUD" --
CLOSED. Not pushed.** "Subject" is not a new domain
entity -- it is the user-facing name for `Activity(kind=ORDINARY)`; no
Subject dataclass or table. Adds `GET/POST
/schools/{school_id}/years/{year_id}/subjects` and `PUT/DELETE
.../subjects/{subject_id}`, backed by `SubjectService`/
`SqlAlchemyActivityRepository`, shaped exactly like Class CRUD and
reusing `persistence/configuration_write_lock.py` entirely unchanged.
Natural IDs are always server-generated as `activity_<uuid4().hex>`;
`kind` is never client-controlled and never mutated after create --
create always persists `ORDINARY`, update/delete only ever resolve an
existing `ORDINARY` target (checked both at the pure-rule level and
authoritatively against the raw ORM row). A `CLUB` activity ID passed
to `/subjects/{id}` is indistinguishable from a missing Subject (`404
"Subject not found"` in both cases). Duplicate `Activity` names within
one `AcademicYear` are rejected (`409 DUPLICATE_SUBJECT`, exact/
case-sensitive/trimmed) checked ONLY against other `ORDINARY`
activities -- an identically-named Subject and Club may coexist,
resolving the earlier open question from the pre-Slice-D correction.
Delete is rejected (`409 SUBJECT_IN_USE`, deterministic
`referenced_by`) whenever a current `TeachingRequirement` or
`ReservedBlock` still references the subject -- the only two direct
`Activity` references in the schema. `GET /subjects` never exposes
`kind`; `GET /config` remains fully unfiltered; Teaching Assignments'
already-`ORDINARY`-only activity options automatically include every
new/renamed Subject with zero sync step and zero code change to that
service. Zero schema/migration impact, zero frontend production
change, zero solver change. Live-validated this session against a new,
local-only, unlocked `subject-crud-review-school`/
`ay-subject-crud-2026` dataset (retained, no `Schedule` generated):
create, read, rename (identity preserved across `/subjects`, `/config`,
Teaching Assignments), duplicate rejection, case-variant and
same-name-as-CLUB coexistence, unused-subject delete, referenced-
subject delete rejection, and CLUB-target PUT/DELETE both returning
`404` with the CLUB row left intact all confirmed through the running
app's real HTTP API; all four prior datasets (`synthetic-school`/
`ay-2026`, `synthetic-review-school`/`ay-review-2026`,
`teacher-crud-review-school`/`ay-teacher-crud-2026`,
`class-crud-review-school`/`ay-class-crud-2026`) reconfirmed unchanged.
Test gate: core `tests -m "not slow"` 283 passed/5 deselected (256
pre-existing + 27 new pure `tests/test_subject_service.py` cases);
`tests_web` 254 passed in one process (217 pre-existing + 37 new: 25
`tests_web/test_subject_api.py` + 12
`tests_web/test_subject_repository.py`), zero DB-reachability skips,
confirmed on two consecutive runs; Teaching Assignment regression
(API + repository) 40/40 and the pre-Slice-D preflight Activity-Kind
tests 26/26 both reconfirmed unregressed; frontend 185/185, build
clean; Alembic unchanged at `cae76cba3c58`, single head, no drift.
Explicitly not part of Slice D: Club CRUD, ReservedBlock CRUD,
`ActivityKind` editing, Teacher/Class changes, TeachingAssignment
semantic changes, ParticipantGroup CRUD, Teacher Availability, School/
AcademicYear CRUD, calendar CRUD, solver changes, auth/user
integration, any frontend production feature. The broader Real-School
Setup MVP is **not** complete -- this is Slice D only. Next
implementation slice per the approved setup contract: **Slice E --
School Setup frontend** (not started).

**Real-School Setup MVP -- Slice E (School Setup frontend): IMPLEMENTED,
REVIEWED, a local desktop browser sanity pass REVIEWED against the
live pilot dataset, COMMITTED, and MERGED to `main` at commit
`8532193` "feat: add school setup frontend" -- CLOSED. Not pushed.**
One route, `/configuration/setup` -> `SchoolSetupPage`, with
a third flat nav link ("School Setup", ordered before "Teaching
Assignments", no "Configuration" dropdown/group) and three local tabs
(Teachers/Classes/Subjects, WAI-ARIA tablist pattern, no nested tab
routes, no count badges, exactly one panel mounted at a time). Each
panel (`TeachersPanel`/`ClassesPanel`/`SubjectsPanel`) is fully
self-contained against the already-CLOSED Slice B/C/D backend
contracts, matching `TeachingAssignmentsPage`'s own architecture:
GET-projection-as-sole-source-of-truth, refetch-after-write (never
optimistic), identical `SCHEDULING_CONFIGURATION_LOCKED` lock-race
handling, the existing `.lock-banner` reused verbatim. Create/edit/
delete deliberately do NOT reuse `AssignmentDrawer` -- a compact
"+ Add ..." button reveals a contained inline create panel, edit
switches a row into place, and delete reuses the existing inline
confirm/cancel pattern (no `window.confirm()`, no new modal). Neither
the canonical WHOLE_CLASS `ParticipantGroup` (Owner Decision #33) nor
`Activity.kind`/CLUB rows are ever exposed. `TEACHER_IN_USE`/
`CLASS_IN_USE`/`SUBJECT_IN_USE` `referenced_by` codes get a
human-readable mapping (Teaching assignments, Teacher availability,
Reserved activities, Subgroups, Merged classes) with a safe raw-code
fallback; `DUPLICATE_CLASS`/`DUPLICATE_SUBJECT` get a plain inline
message; Teachers has no duplicate-name rule anywhere, client-side or
otherwise (proven by a passing two-identical-names test). Zero
backend production change, zero migration, zero
`TeachingAssignmentsPage`/`AssignmentDrawer` change (its 55 tests
reconfirmed unmodified), zero new Owner Decisions -- **Owner Decision
#38 remains unused.** Test gate: frontend `npm test` 259 passed (185
pre-existing + 74 new across API-client/panel/page tests), `npm run
build` clean; backend core `tests -m "not slow"` 283 passed/5
deselected (unchanged); canonical single-process `tests_web` 254
passed (unchanged); Alembic unchanged at `cae76cba3c58`, single head,
no drift. A local desktop browser sanity pass (not the full Slice F
acceptance workflow) was run, twice, against the real dev server
pointed at the canonical, locked `synthetic-school`/`ay-2026` pilot
dataset -- read-only inspection only, no mutation attempted --
confirming page hierarchy, tab bar, lock banner, all three tabs' real
data (Subjects correctly ORDINARY-only, no Club rows), and live
keyboard tab activation all render/behave correctly. The 640px
narrow-width responsive CSS was code-reviewed but not confirmed via a
live resized-browser screenshot in this slice -- a non-blocking
limitation; full responsive browser confirmation remains available for
Slice F if browser tooling permits. **Slice F (the full real-school
browser create/edit/delete acceptance workflow) remains NOT
executed.** Explicitly not part of Slice E: Clubs, Reserved Blocks,
Teacher Availability, Subgroups, Merged Classes, rooms/resources,
calendar/day-period editing, School/AcademicYear CRUD, users/auth,
solver configuration. The broader Real-School Setup MVP is **not**
complete -- this is Slice E only. Next slice per the approved setup
contract: **Slice F -- real-school browser acceptance / setup smoke**
(not started).

**Real-School Setup MVP -- Slice F (Real-school browser acceptance /
setup smoke): EXECUTED, PASSED, CLOSED.** Zero new Owner Decisions;
**Owner Decision #38 remains unused.** A pure acceptance slice against
the already-CLOSED Slices A-E -- zero backend/frontend production
changes, zero migrations, zero test-file changes.

Dedicated acceptance dataset (created and retained as durable local
evidence): `real-school-browser-smoke-school`/
`ay-real-school-browser-smoke-2026` -- a complete, generation-capable
clone of the canonical pilot configuration, seeded via the existing
TEST-ONLY `write_scheduling_problem` writer, with zero `Schedule` rows
at the start. An isolated second Vite dev server (port 5174, env
overrides only -- the canonical port-5173 server and
`frontend/.env.local` both untouched) was used to drive the entire
acceptance workflow through the real browser UI.

Observed passing, through the real browser, against this dataset:
Teacher create/edit; two identically-named Teachers both accepted and
both deleted (no-duplicate-name contract proven live); Class
create/edit with an exact duplicate cleanly rejected inline and the
canonical WHOLE_CLASS group/id never exposed (confirmed internal-only
via read-only DB inspection); Subject create/edit with an exact
duplicate cleanly rejected inline and `kind`/CLUB never exposed
(persisted `kind: ORDINARY` and the `activity_<uuid>` natural ID
confirmed only via read-only inspection); all three new/edited
records appearing automatically in the Teaching Assignments create
drawer with zero manual sync and no CLUB ever offered; a real
temporary Teaching Assignment created referencing all three, which
then correctly blocked deletion of each with the same human-readable
`referenced_by` mapping Slice E documented; the temporary assignment
deleted, followed by successful deletion of all three temporary
reference-data records, restoring the smoke dataset's Teacher/Class/
ORDINARY/CLUB/TeachingRequirement counts to their exact pre-browser
baseline (8/4/8/2/25) with a clean `run_preflight` both before and
after. A real Schedule was then generated through the existing
Timetable page's "Generate schedule" control (never the API called
directly) -- succeeded (`OPTIMAL`, version 1) -- after which School
Setup reloaded locked on all three tabs: every record stayed visible
and readable (Subjects still zero Club rows) while every
Add/Edit/Delete control was visibly disabled with the lock banner
explanation. All five pre-existing canonical/review datasets were
snapshotted before and after the full run and confirmed unchanged.

One accurately-recorded, non-blocking limitation (same as Slice E): a
live narrow-viewport (640px) browser screenshot remained unobtainable
in this session's tooling; the 640px responsive CSS itself was not
touched and remains code-reviewed only. Desktop remains the current
primary product target.

Regression baseline reconfirmed unchanged throughout: frontend 259
passed (one already-known, non-reproducible `TimetablePage.test.tsx`
timing flake isolated and cleared), build clean; backend core 283
passed/5 deselected; `tests_web` 254 passed, zero skips; Alembic
unchanged at `cae76cba3c58`, single head, no drift. `git status`/`git
diff` were empty throughout Slice F -- only local PostgreSQL state (the
new smoke dataset) changed, never a tracked file.

**Slices A through F are now CLOSED. The scoped Real-School Setup MVP
is COMPLETE** -- School Setup (Teachers/Classes/Subjects) -> Teaching
Assignments -> Schedule Generation -> Timetable/read-only
configuration-lock workflow, all proven end-to-end through the real
browser. This does **not** mean the broader SchoolTimetable product is
finished: Teacher Availability editing, Clubs/Reserved Blocks editing,
Subgroups/Merged Classes editing, rooms/resources administration, a
calendar/day-period editor, School/AcademicYear CRUD, authentication/
user management, and other advanced scheduling-policy UI all remain
explicitly outside this MVP's scope, as future work.
