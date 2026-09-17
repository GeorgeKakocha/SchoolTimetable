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

**Owner Decision #38 -- Teacher Availability exposes all three
established `AvailabilityStatus` states: `AVAILABLE`** (sparse
default -- absence of a row; schedulable normally), **`PREFER_NOT`**
(persisted sparse exception; SOFT solver preference -- discouraged via
a weighted objective penalty, never forbidden), **`UNAVAILABLE`**
(persisted sparse exception; HARD solver constraint -- forces the
corresponding lesson variable to zero). All three were already fully
implemented in the domain/solver before this decision; the first
Teacher Availability product/API surface exposes all three from the
start -- `PREFER_NOT` is not a deferred future feature, and this is
not a binary-only surface.

**Teacher Availability Slice A (backend read/bulk-write/persistence/
API): IMPLEMENTED, REVIEWED, real HTTP API reviewed, COMMITTED, and
MERGED to `main` at commit `24c1b04` "feat: add teacher availability
backend" -- CLOSED. Not pushed.** Write unit is one Teacher's complete
desired sparse exception set (never per-cell CRUD): `PUT
.../teacher-availability/{teacher_id}` atomically reconciles
persisted rows to match the request (delete no-longer-desired cells,
update a changed status in place preserving `ordinal`, insert new
cells with the next `ordinal` computed across the whole
`AcademicYear`, sorted by `Day.index`/`Period.index`). An explicit
`AVAILABLE` entry is rejected (`422 INVALID_TEACHER_AVAILABILITY`,
`AVAILABLE_EXCEPTION_MUST_BE_OMITTED`) rather than silently dropped;
an in-request duplicate cell is rejected
(`DUPLICATE_AVAILABILITY_CELL`); an unknown status is rejected
(`UNKNOWN_AVAILABILITY_STATUS`); unknown day/period reuses the
existing generic `UnknownReferenceError`/`422 UNKNOWN_REFERENCE`
contract verbatim; a missing Teacher reuses the existing
`TeacherNotFoundError`/`404 "Teacher not found"` contract verbatim.
`GET .../teacher-availability` is a new dedicated sparse
*exception-only* projection (`PREFER_NOT`/`UNAVAILABLE` rows only,
never `AVAILABLE`) -- `GET /config`'s own `teacher_availabilities`
field is untouched and keeps exposing every persisted row under its
existing, unrelated contract. The new `TeacherAvailabilityRepository`
write port reuses the existing configuration write lock
(Decision #35) and generation-race safety (Decision #36) completely
unchanged -- `SchedulingProblem.teacher_availabilities` was already
covered by the frozen dataclass's own equality, proven by two new
deterministic race tests (write-before-generation-persist aborts
generation; generation-persists-first blocks a later write). A narrow
preflight defense-in-depth diagnostic,
`DUPLICATE_TEACHER_AVAILABILITY_CELL`, was added for an in-memory
problem with two rows for the same cell (impossible once persisted --
the table's composite primary key forbids it). Teacher-delete-blocked-
by-`TEACHER_AVAILABILITY` behavior is unchanged and reconfirmed
unregressed. **Zero migration** -- the `teacher_availability` table's
existing composite natural primary key, `ordinal`/uniqueness, status
check constraint, and FK behavior were already fully sufficient. Zero
frontend production change, zero solver production change (only new
tests were added proving the existing `UNAVAILABLE`-HARD/
`PREFER_NOT`-SOFT behavior against small, deterministic, purpose-built
problems).

A new local-only, unlocked review dataset,
`teacher-availability-review-school`/
`ay-teacher-availability-review-2026` (a full pilot clone, zero
`Schedule`), was created and is retained as durable acceptance
evidence -- live-validated this session against the real dev server's
actual HTTP API: GET/PUT/GET-reflects/`/config`-reflects/empty-clears/
other-teachers-unchanged/no-Schedule all confirmed. All six
pre-existing canonical/review datasets were snapshotted (including
`TeacherAvailability` counts) and confirmed unchanged.

Test gate: core `tests -m "not slow"` 309 passed/5 deselected (283
pre-existing + 26 new); canonical single-process `tests_web` 288
passed (254 pre-existing + 34 new), zero DB-reachability skips,
confirmed on two consecutive runs; existing Teacher-delete-blocker,
`/config` serializer, persistence mapper/schema, and
`UNAVAILABLE`-HARD solver tests all reconfirmed unregressed; frontend
259 passed (unchanged), build clean; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Explicitly NOT part of this
slice: any frontend Teacher Availability surface, a per-cell write
endpoint, immediate-save behavior, Club/ReservedBlock UI, a calendar
editor, any Teacher CRUD change, any migration. **Availability B
(frontend page/grid) has not been implemented.** The overall Teacher
Availability phase is **not** complete. Next planned slice: **Teacher
Availability Slice B -- frontend page/grid** (not started).

**[Historical -- superseded by the closure entry below.] Teacher
Availability Slice B (frontend page/3-state matrix) --
implemented and PENDING TECHNICAL/VISUAL REVIEW. Not committed, not
merged, not pushed**, left uncommitted on
`feature/teacher-availability-frontend` per explicit process
instruction; this entry does not close Availability B or the overall
Teacher Availability phase. **Owner Decision #39 was NOT created.**
Consumes the Slice A backend contract exactly as merged, zero
backend/persistence/solver/migration change. Adds: mirrored types in
`api/types.ts`; `api/teacherAvailability.ts`
(`getTeacherAvailability`/`replaceTeacherAvailability`); route
`/configuration/teacher-availability` and its nav link (order:
Timetable, School Setup, Teacher Availability, Teaching Assignments);
`TeacherAvailabilityPage.tsx` (owns page/draft/dirty state);
`AvailabilityGrid.tsx` (Period rows x Day columns desktop, matching
`TimetableGrid`'s orientation; unconditionally-rendered per-Day mobile
markup, single 640px CSS toggle, no JS viewport listener) and
`AvailabilityLegend.tsx` (always-visible), both purely presentational.
Each cell is one real `<button>` cycling AVAILABLE -> PREFER_NOT ->
UNAVAILABLE -> AVAILABLE with a dynamic `aria-label` (no
`aria-pressed`/`aria-checked`) plus one shared `aria-live` region.
Dirty state is derived, never manually toggled; Save does a whole-
Teacher PUT then an authoritative GET refetch/draft rebuild (never
optimistic); Reset is client-only; a `409
SCHEDULING_CONFIGURATION_LOCKED` race during Save discards the draft,
refetches, and goes read-only. Bulk actions are explicitly out of
scope.

Frontend test gate: 40 new/modified tests (10 `App.test.tsx`, 9
`api/teacherAvailability.test.ts`, 29
`pages/TeacherAvailabilityPage.test.tsx`); full suite 259 + 40 = **299
passed**, zero skips, confirmed clean across six consecutive full
18-file-suite runs after fixing a real test race (a "wait for the
page heading" helper matched both the loading and ready states, since
both render an identical `<h1>`; replaced with ready-state-specific
waits throughout); existing School Setup/Teaching
Assignments/Timetable/App-routing suites (120 tests) reconfirmed
unregressed; build clean, `dist/` removed. Backend regression
reconfirmed unchanged, though untouched: core 309 passed/5 deselected;
`tests_web` 288 passed, zero skips; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Scope audit confirmed changes
confined to `frontend/src/` plus this docs update -- zero backend,
zero migration, zero dependency change. Explicitly NOT executed:
Availability C, any bulk-edit action, any Teacher CRUD change. The
overall Teacher Availability phase is **still not complete** -- this
records an implemented-but-unreviewed candidate pending the owner's
review before any commit, merge, or further slice.

**[Historical -- superseded by the closure entry below.] Teacher
Availability Slice B -- review-blocking correctness defect
found and corrected. STILL PENDING REVIEW, still not committed,
merged, or pushed.** A stale-authority safety gap in
`TeacherAvailabilityPage.tsx`: after a successful PUT, if the
mandatory authoritative GET refetch itself failed (`refreshState`
`stale`), Save/Reset/the Teacher selector stayed gated only on
`isDirty`/`isSaving`/`configuration_locked` -- never on
`refreshState.status` -- so they could remain actionable (Reset could
even restore the draft from the now-superseded pre-write projection)
while the server had already diverged. The same gap applied to the
`409 SCHEDULING_CONFIGURATION_LOCKED` lock-race path when its own
post-race refetch failed. Fixed by applying the page's existing
`controlsDisabled` derivation (`refreshState.status !== "idle" ||
isSaving`, matching `TeachingAssignmentsPage`'s own precedent name and
shape) uniformly to the grid, Save, and Reset, and to the Teacher
selector without the `locked` term -- a locked-but-authoritative,
non-stale projection must still permit Teacher browsing; locked
(read-only-but-authoritative) and stale (authority-unknown) are
distinct states. `handleSave`/`handleReset`/`handleTeacherChange` each
also gained a defensive `refreshState.status !== "idle"` guard in the
handler body, not just the button's `disabled` attribute.

Three new tests were added under a `describe` block reusing
`TeachingAssignmentsPage.test.tsx`'s own exact stale-projection-safety
naming: successful-Save/failed-refresh (old data stays visible, the
PUT is never reported as failed, every mutation control disables, a
disabled control cannot cause a second PUT, Retry recovers to a clean
dirty state); lock-race whose own post-race refresh also fails (same
full disabling, Retry then loads the authoritative locked projection
with the Teacher selector re-enabled but Save/Reset/cells still
disabled because of `locked`); and a 404-Teacher-not-found write whose
refetch also fails (confirming the shared stale-handling path covers
it, no bespoke second state machine needed).
`TeachingAssignmentsPage.tsx`/`.test.tsx` were not touched; its own
three stale-projection-safety tests were rerun unmodified and still
pass.

Corrected, precise test accounting (the prior entry's "10 + 9 + 29 =
40" added a modified file's post-change total to two new files'
totals, which is not a valid net-new count): `App.test.tsx` carries 8
tests on `main` and 10 now (net-new **2**); `api/
teacherAvailability.test.ts` is new with 9 tests (net-new **9**);
`pages/TeacherAvailabilityPage.test.tsx` is new with **32** tests (29
original + 3 added by this correction, net-new **32**). Actual
net-new relative to the 259-test `main` baseline: 2 + 9 + 32 = **43**.
Full suite: 259 + 43 = **302 passed**, zero skips, matching three
consecutive full 18-file-suite runs exactly; build clean, `dist/`
removed. Backend untouched: `git diff --name-only -- src/
school_timetable tests tests_web alembic` empty; Alembic unchanged at
`cae76cba3c58`, single head, no drift. An incidental untracked
repo-root `uv.lock` (a byproduct of running `uv run` in a prior
session) was removed and never staged; no dependency changed. Owner
Decision #39 was **not** created; Availability C was **not**
executed. The overall Teacher Availability phase remains **not
complete** -- this correction does not close Availability B; it
remains implemented, now defect-corrected, and still uncommitted on
`feature/teacher-availability-frontend`, pending the owner's review.

**Teacher Availability -- Availability B (frontend page / 3-state weekly
matrix): IMPLEMENTED, REVIEWED, technical re-review PASSED, read-only
visual review PASSED, COMMITTED, MERGED to `main`, CLOSED.**
Implementation commit `ca464e26a7b365d040467176ecabd97716ee604d`
("feat: add teacher availability frontend"), fast-forward merged to
`main` from `feature/teacher-availability-frontend` (now deleted).
This entry is the authoritative current-state record; the two
preceding historical entries above capture the review process
(initial implementation, then the stale-authority defect found and
corrected) and are superseded by it.

Route: `/configuration/teacher-availability`. Nav order: Timetable,
School Setup, Teacher Availability, Teaching Assignments. Desktop
editor: Period rows, Day columns, instructional Periods only. Mobile:
per-Day stacked sections, <=640px, CSS-driven, no viewport JS. State
model: Available, Prefer not, Unavailable, cycling Available -> Prefer
not -> Unavailable -> Available. Accessibility: native buttons, no
`aria-pressed`/`aria-checked`, a dynamic accessible name, visible
symbol + text, one shared `aria-live` region. Draft: the sparse
selected-Teacher exception set; `AVAILABLE` is the absence of an
exception. Save: whole-Teacher PUT -> mandatory authoritative GET
refetch -> draft rebuild. Reset: client-only. No bulk action. Dirty
state is derived from a normalized draft-vs-authoritative comparison;
the Teacher selector disables while dirty.

Stale-authority safety: after a successful PUT whose refetch fails,
old/stale data may remain visible while every mutation entry point --
cells, Save, Reset, the Teacher selector -- disables, with Retry the
only recovery path; identical behavior after a `409` lock race whose
own post-race refetch fails; and after a `404` write whose refetch
fails, via the same shared stale handling. Once an authoritative
locked projection loads (not stale), the Teacher selector re-enables
and data stays readable while cells/Save/Reset stay disabled because
of the lock itself. Regression coverage: successful-Save/failed-
refresh, lock-race/failed-refresh, and 404/failed-refresh, all passing.

Precise frontend test baseline: `main`-before-slice 259; net-new 43;
final **302**, 18 test files, zero skips; three consecutive full runs
302/302/302, both before this merge and reconfirmed after it on
`main`; build clean. Backend: zero change; Alembic unchanged at
`cae76cba3c58`, single head, no drift; zero migration; zero dependency
change; no `uv.lock`.

Owner Decision #38: **LOCKED**. Owner Decision #39: **does not
exist**. Availability C: **NOT executed** -- next planned slice is
Availability C (real-browser write/persistence + solver acceptance).
Teacher Availability overall phase: **NOT complete** -- Availability
B's closure is scoped to the frontend page only. Nothing pushed.

**Teacher Availability -- Availability C (real-browser write /
persistence / solver / lock acceptance): EXECUTED, PASSED, CLOSED.**
Pure acceptance -- zero production source diff (frontend, backend,
migrations all unchanged, confirmed empty before and after). Two new,
local-only, dedicated SchedulingProblem datasets were created via the
existing TEST-ONLY `problem_writer.py`, derived from the small
deterministic two-teacher minimal-problem pattern already used by
`tests/test_teacher_availability_solver.py` rather than the full
40-period pilot.

Choice dataset (`teacher-availability-browser-choice-school` /
`ay-teacher-availability-browser-choice-2026`, one day, three
periods A/B/C): via a temporary isolated frontend instance and the
real backend, B was set to `Prefer not` and C to `Unavailable`
through the real cell buttons and a real Save (one PUT, authoritative
refetch); PostgreSQL confirmed exactly those two persisted rows and
none for A; a real page reload reproduced all three states; `/config`
carried both rows. Generation through the real "Generate schedule"
control succeeded (`OPTIMAL`, `total_soft_penalty=0`); the target
Teacher's only `ScheduleEntry` was at A, with zero entries at B or C
-- proving `UNAVAILABLE` is HARD (C never used) and `PREFER_NOT` is
avoided when an equivalent `AVAILABLE` alternative exists (B skipped
at zero cost). Post-generation the page showed the lock banner, both
states still readable, cells/Save/Reset disabled, and the Teacher
selector still usable. A supporting direct API `PUT` against the
locked dataset returned `409 SCHEDULING_CONFIGURATION_LOCKED` with
zero mutation.

Soft-required dataset (`teacher-availability-browser-required-school`
/ `ay-teacher-availability-browser-required-2026`, one day, two
periods; B pre-seeded `UNAVAILABLE` at creation, making A the only
feasible slot): the real cell button set A to `Prefer not` and saved;
persisted and reload-confirmed. Generation succeeded (`OPTIMAL`,
`total_soft_penalty=5` -- a genuine nonzero soft cost, not silent
infeasibility), with the target Teacher's only entry at A --
`Prefer not`, proving `PREFER_NOT` never blocks a placement even when
it is the only option. Post-generation lock behavior independently
reconfirmed on this second dataset.

Responsive: the same known tooling limitation from Slices B/E/F
recurred (`resize_window` did not change the rendered viewport);
reported honestly, non-blocking, matching Availability B's own green
automated responsive coverage.

All seven pre-existing canonical/review datasets were snapshotted
before and after this run across the same recorded
Teacher/Class/ORDINARY/CLUB/TeachingRequirement/TeacherAvailability/
Schedule fields -- identical throughout; the two new Availability C
datasets are excluded from that claim (intentionally created/mutated)
and are retained as durable acceptance evidence, never cleaned up.

Final regression identical to baseline: frontend 302 passed/18
files/zero skips (one isolated single-test flake observed and
reconfirmed non-reproducible on immediate rerun, matching the
project's known environmental full-suite-contention pattern, not a
regression), build clean; backend core 309 passed/5 deselected;
`tests_web` 288 passed, zero skips; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Zero production source
changes, zero test-source changes, zero migration, zero dependency
change. Owner Decision #39 was not created.

**The Teacher Availability phase is now COMPLETE and CLOSED**:
Availability A CLOSED, Availability B CLOSED, Availability C CLOSED.
Owner Decision #38 remains the sole authoritative locked decision.
This closure does **not** mean Teacher workload policies, gap
minimization UI, Clubs/Reserved Blocks, rooms/resources,
subgroups/merged classes, or a calendar editor are complete. **Next
planned product phase: Clubs / Reserved Blocks.**

**[Historical -- superseded by the closure entry below.] Reserved
Activities -- Reserved A1 (Special Activity catalog
backend): IMPLEMENTED on `feature/special-activity-backend`. Technical
review PASSED, real HTTP API review PASSED, automated gates PASSED.
NOT yet committed, NOT merged, NOT pushed.** Corrected
terminology: **Special Activity** = `Activity(kind=CLUB)` (catalog
data, this slice); **Reserved Activity** = a `ReservedBlock`
(scheduled reservation, belongs to Reserved A2, not implemented here).
Internal `CLUB` enum/persistence value unchanged, never renamed, never
exposed raw. Reuses the existing `Activity` domain object and
`activity` ORM table verbatim -- zero schema change, zero migration.

CRUD: `name` only, server-generated `activity_<uuid4().hex>` natural
ID, `kind` always `CLUB` server-side. Duplicate rule: exact
case-sensitive trimmed name rejected only among CLUB activities;
identical ORDINARY name never conflicts (and vice versa) -- exact
mirror of Subject CRUD. Ordinal: shared `AcademicYear`-wide `Activity`
sequence across both kinds. Delete blocked only by a genuine
`ReservedBlock` reference (`SPECIAL_ACTIVITY_IN_USE`). Kind isolation:
an ORDINARY activity ID behaves as 404 `"Special activity not found"`
on every route, never mutated.

**Mandatory derived-name synchronization** (a consistency consequence
of the corrected contract, not Owner Decision #39): `ReservedBlock.name`
is server-derived from its Activity's `name`. Renaming a Special
Activity atomically synchronizes every referencing `ReservedBlock.name`
in the same transaction -- proven for single and multiple referencing
blocks, an unrelated block left untouched, and zero partial
synchronization on a failed rename. Timetable projections are
unchanged (still display `Activity.name` only).

Configuration lock and generation-race safety both reuse existing,
unchanged infrastructure (`configuration_write_lock.py`;
`SchedulingProblem`'s frozen-dataclass equality already covers
`activities`/`reserved_blocks`) -- proven with two new deterministic
race tests mirroring the Subject-write precedent. New repository port
`SpecialActivityRepository` is a deliberate sibling to
`ActivityRepository`, not an extension of it (preserving that port's
own "no Club management" guarantee). New routes:
`GET/POST .../special-activities`, `PUT/DELETE
.../special-activities/{id}`, error mapping mirrors `subject_routes.py`
exactly. `/config` contract unchanged, now correctly reflects
synchronized `ReservedBlock.name` values after a rename.

Test gate: 26 new pure service tests (core 335 passed/5 deselected);
15 new repository + 25 new API tests (`tests_web` 328 passed, zero
skips); frontend 302 passed (fully unchanged, zero frontend files
touched), build clean; Alembic unchanged at `cae76cba3c58`, single
head, no drift.

A new local-only review dataset,
`special-activity-review-school`/`ay-special-activity-review-2026`,
was created and validated end-to-end against the real running backend
(GET/POST/duplicate-reject/same-name-ORDINARY-allowed/
rename-with-block-sync/referenced-delete-reject/
temporary-create-delete) and is retained. **Seeded initial state:**
one ORDINARY Subject ("Mathematics"), one CLUB Special Activity
("Robotics Club"), one referencing `ReservedBlock`, zero Schedule.
**Retained final state** (read-only re-confirmed at closure): four
Activities -- the original ORDINARY "Mathematics" unchanged; the CLUB
activity renamed "Robotics Club" -> "STEM Lab"; two further new CLUB
activities created and retained ("Debate Club", and a second
"Mathematics" proving same-name-as-ORDINARY coexistence); one
`ReservedBlock` with its `name` synchronized to "STEM Lab"; zero
Schedule throughout (a separate temporary Special Activity created
during review was deleted and leaves no residue).

The nine pre-existing datasets -- the seven earlier canonical/review
datasets plus the two Availability C acceptance datasets -- were
snapshotted before and after this run across the same recorded fields
(now including `ReservedBlock` counts) -- unchanged for all nine; the
new review dataset is excluded from that claim.

Explicitly NOT part of this slice: `ReservedBlock` CRUD, the Reserved
Activities page, the School Setup Special Activities frontend tab, any
frontend change, Resource/ParticipantGroup support, any of the five
`ReservedBlock`-specific preflight/write-validation corrections recon
identified (activity-kind, class-slot collision, teacher-slot
collision, Teacher-UNAVAILABLE rejection, instructional-only slots) --
all belong to Reserved A2. **Owner Decision #39 was NOT created.**
Reserved A1 is **not** closed; Reserved A2/B/C remain unimplemented.

**Reserved Activities -- Reserved A1 (Special Activity catalog
backend): IMPLEMENTED, REVIEWED, real HTTP API review PASSED,
COMMITTED, MERGED to `main`, CLOSED.** Implementation commit
`0b64e115791e4da2bbc243d6d5ec848f28c9d064` ("feat: add special
activity backend"), fast-forward merged to `main` from
`feature/special-activity-backend` (now deleted). This entry is the
authoritative current-state record, superseding the preceding
historical pending-review entry above.

Contract reconfirmed at closure: Special Activity = `Activity
(kind=CLUB)`, zero schema/migration change; `name`-only CRUD;
CLUB-scoped exact/case-sensitive/trimmed duplicate rule, same-name
ORDINARY always allowed; server-generated `activity_<uuid4().hex>`;
shared `AcademicYear`-wide `Activity` ordinal across both kinds;
delete blocked only by a `ReservedBlock` reference; ORDINARY target
behaves as 404 everywhere, repository-level kind check independent of
the `validate` callback. Mandatory derived-name invariant: rename
atomically synchronizes every referencing `ReservedBlock.name` in the
same transaction (single/multiple blocks proven, unrelated block
untouched, zero partial sync on failure); `/config` unchanged.
Configuration lock and generation-race protection (both orderings)
reuse existing infrastructure unchanged.

**Review dataset final retained state** (re-confirmed read-only at
closure): `special-activity-review-school`/
`ay-special-activity-review-2026`, seeded with one ORDINARY Subject
and one CLUB Special Activity with one referencing `ReservedBlock`,
now retains four Activities (the original ORDINARY unchanged; the
CLUB renamed "Robotics Club" -> "STEM Lab"; two further new CLUB
activities created and retained, "Debate Club" and a second
"Mathematics"), one `ReservedBlock` with its name synchronized to
"STEM Lab", and zero Schedule; a separate temporary Special Activity
created during review was deleted and leaves no residue. Retained as
durable acceptance evidence.

Final regression: 26 pure + 40 repository/API new tests; core **335
passed/5 deselected**; `tests_web` **328 passed, zero skips**;
frontend **302 passed/18 files/zero skips**, fully unchanged, build
clean; Alembic unchanged at `cae76cba3c58`, single head, no drift. The
nine pre-existing datasets (seven earlier canonical/review + two
Availability C) remain unchanged across the same recorded fields at
closure; the new review dataset stays excluded from that claim. Zero
migration, zero frontend change, zero domain/ORM change, zero solver
production change beyond the reviewed scope.

Owner Decision #38: **LOCKED, unchanged**. Owner Decision #39: **not
created**. **Reserved A2: NOT implemented. Reserved B: NOT
implemented. Reserved C: NOT executed.** Nothing pushed. **Next
planned slice: Reserved A2 -- `ReservedBlock` backend CRUD +
validation/preflight.**

**Reserved Activities -- Reserved A2 (Reserved Activity /
`ReservedBlock` backend): CLOSED ON MAIN.** Implementation commit
`ab15e6a` (`ab15e6a0ea645b6160d62941eb2609b87b09a96f`, "feat: add
reserved activity backend"), fast-forwarded onto `main` directly after
`8bfd9e8` (no merge commit). "Reserved Activity" = the
user-facing name for `ReservedBlock`, mirroring "Special Activity" =
`Activity(kind=CLUB)`. Reuses the existing `ReservedBlock` domain
object and `reserved_block`/`reserved_block_class_section`/
`reserved_block_slot` ORM tables verbatim -- zero schema change, zero
migration.

CRUD: POST/PUT accept the complete aggregate (`special_activity_id`,
`class_section_ids`, `teacher_id` required-but-nullable, `slots`) as
one whole-aggregate replacement, never partial. `ReservedBlock.name`
always server-derived from the Special Activity's current name.
Natural ID `reserved_block_<uuid4().hex>`; ordinal is `ReservedBlock`'s
own sequence. Canonical child ordering (authoritative `ClassSection`
order; `Day.index`/`Period.index` order), always recomputed at write
time regardless of request order -- required for Owner Decision #36's
frozen-dataclass generation-race equality to hold. Update replaces
children wholesale (delete-and-reinsert, one transaction).

Five semantic invariants, each enforced at write-time and independent
preflight: (1) activity must be a Special Activity -- preflight
`RESERVED_BLOCK_NON_CLUB_ACTIVITY` (matches the established
`NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY` naming convention),
application `NonSpecialActivityTargetError` -> 422
`NON_SPECIAL_ACTIVITY_TARGET` with zero raw `CLUB`/`ORDINARY`/
`actual_kind` leak; (2) instructional slots only; (3) a
teacher-attached block may never use a Teacher-`UNAVAILABLE` slot
(`PREFER_NOT`/`AVAILABLE` both allowed, zero solver penalty --
`ReservedBlock` remains fixed occupancy, zero CP-SAT variables,
confirmed unchanged); (4) no two different blocks may claim the same
class+slot; (5) no two different teacher-attached blocks may claim the
same teacher+slot. Plus two same-block structural defense-in-depth
diagnostics mirroring `DUPLICATE_TEACHER_AVAILABILITY_CELL`'s own
precedent. All five checks live once in `validation/preflight.py`;
the application layer reuses them via a candidate-diff pattern
mirroring `teaching_assignment_rules.new_validation_errors` exactly,
so write-time and independent-verifier checks can never diverge. The
collision scan is a direct deterministic traversal (never
`ProblemIndex`'s last-writer-wins solver lookups), emitting exactly
one directional diagnostic per collision; a block can never collide
with its own prior self since an update's candidate always replaces
the target rather than duplicating it.

Fixed, unrelated pre-existing bug found along the way:
`tests/test_reoptimize.py` had a hand-built `Activity("club_chess",
"Chess")` missing `kind=ActivityKind.CLUB` (invisible before this
slice); corrected the test fixture, not the new validation.

New routes: `GET/POST .../reserved-activities`, `PUT/DELETE
.../reserved-activities/{id}` -- never `/reserved-blocks`; raw
`/config` unchanged. `ReservedActivityProjectionService` loads exactly
ONE `SchedulingProblem` snapshot for the whole page (never composes
`SpecialActivityProjectionService`). Every `reserved_activities` item
is normalized (bare ID references only, resolved against the same
response's top-level catalogs) -- never denormalized names, `kind`,
or `ordinal`. `periods` returns every period with `is_instructional`;
writes accept instructional periods only.

Repository (`SqlAlchemyReservedActivityRepository`) independently
re-resolves every reference scoped to the same `academic_year_id` and
re-verifies the Special Activity's kind regardless of `validate`
(proven via a deliberately permissive fake). Delete is a leaf
operation: children cascade at the DB level; `schedule_entry`'s
`RESTRICT` FK to `reserved_block.id` is structurally unreachable since
the configuration lock already forbids the delete once any Schedule
exists.

Test gate: 46 new pure + 17 new preflight tests (core 398 passed/5
deselected); 30 new repository (including 5 dedicated cross-
AcademicYear defense-in-depth tests added during the pre-closure
audit) + 38 new API tests (`tests_web` 396 passed, zero skips);
frontend 302 passed/18 files, zero skips (fully unchanged), build
clean; Alembic unchanged at `cae76cba3c58`, single head, no drift.

**Pre-closure audit result: A. RESERVED A2 PRE-CLOSURE AUDIT PASSED**
(run twice, identical results, zero drift). Cross-AcademicYear
repository defense-in-depth explicitly proven via dedicated tests with
a deliberately permissive fake `validate` callback: cross-AY UPDATE
target rejected, cross-AY DELETE target rejected, UPDATE onto an
`ORDINARY` activity rejected even with a permissive validator, UPDATE
onto a Special Activity from another `AcademicYear` rejected, and a
representative other-AY `ClassSection` reference rejected on CREATE --
all confirmed zero-partial-mutation. Owner Decision #36's
generation-vs-write race protection reconfirmed unchanged
(`SchedulingProblem.reserved_blocks` was already part of the frozen-
dataclass equality `persist_initial_version` uses).

**Final verification baseline (post-merge, on `main`):** core 398
passed/5 deselected; `tests_web` 396 passed, zero skips; frontend 302
passed/18 files, zero skips; frontend build clean; Alembic
`cae76cba3c58`, one head, no drift.

A new local-only review dataset,
`reserved-activity-review-school`/`ay-reserved-activity-review-2026`
(3 days, 4 instructional + 1 non-instructional period, 3 Teachers, 3
ClassSections, 2 CLUB + 1 ORDINARY Activity, Teacher A with one
UNAVAILABLE + one PREFER_NOT row, one pre-existing ReservedBlock, zero
Schedule), was created and validated end-to-end against the real
running backend across all 19 required proof points (projection
shape; teacherless and teacher-attached create; canonical class/slot
reordering; duplicate class/slot rejection; wrong-kind rejection with
zero leak; non-instructional rejection; UNAVAILABLE rejection
bundling correctly with a simultaneous collision; PREFER_NOT
acceptance; both collision kinds; PUT replacement recomputing the
derived name, reflected in `/config`; DELETE reflected in `/config`;
zero Schedule throughout) and is retained.

**Seeded initial state:** one `ReservedBlock`, `club_debate_block`.
**Final retained state** (re-confirmed read-only from PostgreSQL at
pre-closure audit): `club_debate_block` was renamed (via the PUT
proof step, Special Activity changed, name recomputed to "Art Club")
then deleted (via the DELETE proof step) -- zero rows remain under
that original ID. Five other blocks, created across the review
sequence (teacherless create, teacher-attached create,
reversed-class-order create, reversed-slot-order create,
PREFER_NOT-allowed create), remain retained. **Final `ReservedBlock`
count: 5. Final `Schedule` count: 0.** (Corrects a prior informal
miscount of four retained blocks -- five is the PostgreSQL-confirmed
total.)

All ten pre-existing datasets (seven earlier canonical/review + two
Availability C + Reserved A1's own `special-activity-review-school`)
snapshotted before and after this run across the same recorded fields
-- unchanged for all ten; the new review dataset is excluded from
that claim.

Explicitly NOT part of this slice: the Reserved Activities frontend
page, any frontend change, Resource/ParticipantGroup support, multiple
Teachers per block, recurrence/flexible placement, any timetable or
solver production change (both fully unchanged -- the five invariants
are pure preflight/application validation, never a solver behavior
change). **Owner Decision #39 was NOT created** -- every open question
was settled by direct existing precedent.

**Reserved A2 is CLOSED ON MAIN** (implementation commit `ab15e6a`).

**Reserved Activities -- Reserved B (Special Activities + Reserved
Activities frontend): CLOSED ON MAIN.** Implementation commit
`81aef7f` (`81aef7f73460e5ae5828a43dcdd6f1c3942bcdd2`, "feat: add
reserved activities frontend"), fast-forwarded onto `main` directly
after `dc22655` (no merge commit). Frontend-only, zero backend/
migration/domain/solver change; `api/types.ts` unchanged (only
`ValidationDiagnostic` is imported from it).

School Setup tabs: Teachers, Classes, Subjects, Special Activities.
Flat nav: Timetable, School Setup, Teacher Availability, Teaching
Assignments, Reserved Activities (last). A new narrow, typed, one-shot
`location.state` tab-target mechanism
(`SchoolSetupNavigationState`/`isSchoolSetupTabKey`) lets Reserved
Activities' prerequisite links land on School Setup with Special
Activities or Classes pre-selected; a direct/reload visit still
defaults to Teachers, and normal tab clicks stay fully local.

Special Activities tab: full CRUD (`SpecialActivitiesPanel.tsx`,
structural copy of `SubjectsPanel.tsx`) with help/empty/lock/stale
states and `SPECIAL_ACTIVITY_IN_USE` mapped to human text, never
leaking `RESERVED_BLOCK`.

Reserved Activities page (`/configuration/reserved-activities`):
stacked summary cards; one full-width contained Add/Edit editor panel
(never a modal/drawer); Special Activity single-select (no default);
Class checkbox list (`fieldset`/`legend`); optional Teacher select
("No teacher" default); instructional-only Period x Day checkbox
matrix with a CSS-only <=640px per-Day layout (own component, not a
reuse of `AvailabilityGrid` -- set membership, not a 3-state status);
unique desktop/mobile checkbox ids with their own labels; no bulk slot
actions; backend-authoritative semantic validation
(`INVALID_RESERVED_ACTIVITY` diagnostics mapped to human text via the
current projection's catalogs, including collision-target resolution);
a combined page-level prerequisite surface for missing Special
Activities/Classes/instructional calendar data (never a broken
editor); stale-authority and lock-race protection reusing the existing
patterns exactly, extended with one new `referenceStale` write outcome
(`UNKNOWN_REFERENCE`/`NON_SPECIAL_ACTIVITY_TARGET`) that closes the
editor and discards its draft rather than preserving it; defensive
"Unknown ..." fallback rendering for any unresolvable saved reference.

Test gate (affected-file focused execution totals, not "new tests" --
`SchoolSetupPage.test.tsx`/`App.test.tsx` already carried pre-existing
tests): **B1 focused execution: 42 passed across 3 files.** **B2
focused execution: 77 passed across 6 files.** **Combined focused
execution: 119 passed across 9 distinct files.** (Corrects an earlier
"focused B1+B2 gate 76 passed/6 files" entry, which conflated the
B2-only group with the combined total and used a pre-audit count;
also reflects two pre-closure-audit additions -- an executable proof
that School Setup's `location.state` tab-target hint is actually
cleared to `null`, and a two-record proof that opening one Reserved
Activity's editor disables Add, the other record's Edit, and both
records' Delete.) The authoritative **full suite total is 402
passed/25 files/zero skips** (400 pre-audit + the same 2 audit-added
tests); build clean. No backend tracked files changed in Reserved B;
backend regression baselines remained 398 passed/5 deselected and
`tests_web` 396 passed/zero skips. Alembic remained `cae76cba3c58`
with one head and no drift. A lightweight manual browser pass against
the real dev server confirmed live end-to-end rendering (nav/tab
order, existing Reserved Blocks, locked state) -- not Reserved C
acceptance, no data mutated, no Schedule created.

**Owner Decision #39 was NOT created.**

**Reserved B is CLOSED ON MAIN** (implementation commit `81aef7f`).

**Reserved C (real-browser/persistence/solver/timetable/lock
acceptance): PASSED**, across two sessions on one retained dataset
(`reserved-activity-c-acceptance-school`/
`ay-reserved-activity-c-acceptance-2026`). The first session completed
all CRUD/collision/validation proofs then was genuinely **BLOCKED** by
a browser-viewport-tooling gap (`resize_window` was a proven no-op) --
that history is preserved, not erased. This session resumed from the
retained pre-generation checkpoint (2 ReservedBlocks, 0 Schedules) and
resolved the gap by launching a separate, genuinely narrow real Chrome
instance (already-installed `google-chrome --headless=new`, no new
project dependency) driven directly via Chrome DevTools Protocol
(`Emulation.setDeviceMetricsOverride`, using the system Python's
already-installed `websockets`/`requests`) against the same live app --
confirmed `innerWidth: 375, clientWidth: 360`, mobile slot layout shown/
desktop hidden, zero overflow within the Reserved Activities editor's
own DOM. A 93px page-level overflow was found (`clientWidth 360` vs
`scrollWidth 453`), traced exclusively to the shared `AppShell` top nav
bar, reproduced identically on the pre-existing, unrelated Teacher
Availability page.

**Correction (post-closure technical review):** that overflow was
originally classified "pre-existing/out-of-scope" and Reserved C was
closed anyway (commit `072aea7`) -- **too permissive** a reading of
Reserved C's own explicit "no material page-level horizontal overflow"
criterion, which applies regardless of whether the responsible markup
is Reserved-Activities-owned or shared shell chrome. It was therefore
a real acceptance blocker, corrected in follow-up commit `865138d`
("fix: wrap mobile app navigation" -- `865138dabbb7f1bec395aa64d624c530ea115880`):
one `@media (max-width:640px) { .app-nav { flex-wrap: wrap; gap:
0.6rem 1rem; } }` rule; `AppShell.tsx` unchanged; all five nav links
remain directly visible, in order, wrapping cleanly with no hamburger/
dropdown/JS viewport handling; desktop unaffected above 640px. One new
regression test was added (`App.test.tsx`, confirming the `app-nav`
CSS hook and all five links remain present). No Reserved Activities
business logic was ever defective.

**Re-run narrow acceptance after the fix** (zero tolerance for the
previous overflow): Reserved Activities/Teacher Availability/School
Setup all measured `375 / 360 / 360` (innerWidth/clientWidth/
scrollWidth) -- `scrollWidth` exactly equals `clientWidth` on all
three, zero overflow. All five nav links visible/ordered on every
page, clean wrap, no clipping, no overlap with page content. Reserved
Activities editor (opened against the already-existing unlocked
`reserved-activity-review-school` dataset -- the locked
`reserved-activity-c-acceptance-school` was correctly left untouched):
zero whole-page overflow, mobile layout shown/desktop hidden, Save/
Cancel reachable, Cancel exercised without saving. Desktop @1280px
reconfirmed: nav one row, matrix visible, mobile hidden -- zero
desktop regression. This is a corrective re-run of the one failed
gate, not a Reserved C restart -- every other piece of Reserved C
evidence below remained valid and was not repeated.

A deliberate 3-teacher dataset construction (Teacher C, added solely to
keep the acceptance dataset solver-feasible once Teacher A is both
reserved and made UNAVAILABLE at a slot that would otherwise strand one
of 8A's ordinary periods) was dry-run verified before any browser step:
preflight errors = 0, solver status = OPTIMAL, total_soft_penalty = 0.

Live evidence (real browser + real API + direct PostgreSQL): class
collision and Teacher-UNAVAILABLE rejections with exact human-readable
messages and preserved drafts; PREFER_NOT reservation created
successfully; full temporary Special/Reserved Activity CRUD lifecycle
(create/rename/in-use-blocker/whole-aggregate-edit/delete) with zero
internal-terminology leaks; persistence and `/config` both matched the
browser-visible pre-generation state exactly; real-browser generation
succeeded with persisted `solver_status = OPTIMAL`,
`total_soft_penalty = 0`; independent-verifier pass proven structurally
(the codebase's own generation service makes persistence impossible
unless `verification.verifier.verify(...)` returns `passed=True`
first, and a `Schedule` row now exists); Class timetables show Assembly
fixed at 8A Monday/Period 1 and Debate Club (Teacher A) fixed at 8B
Monday/Period 1, neither moved by the solver; Teacher A's timetable
shows Debate Club at Monday/Period 1 with no simultaneous ordinary 8A
lesson, an empty UNAVAILABLE slot, and never shows the teacherless
Assembly; exact-full occupancy confirmed via direct PostgreSQL
aggregation (6/6 cells for both 8A and 8B); `ScheduleEntry` persistence
confirmed (12 rows: 10 `REQUIREMENT` + 2 `RESERVED_BLOCK`); post-
generation UI lock identical on both management surfaces
("Scheduling configuration is locked because a schedule already
exists."); direct API `POST`/`PUT`/`DELETE` all returned `409
SCHEDULING_CONFIGURATION_LOCKED` with `GET` remaining `200`/
`configuration_locked: true` and zero partial mutation confirmed via
before/after PostgreSQL counts.

Final retained C dataset: Teachers 3, ClassSections 2, ORDINARY
Activities 2, Special Activities 2, TeachingRequirements 3,
TeacherAvailability 2, ReservedBlocks 2, Schedules 1, ScheduleEntries
12 -- kept as durable acceptance evidence, not cleaned up.

Pre-existing dataset safety: all eleven pre-existing datasets snapshotted
across the same recorded practical count fields -- unchanged for all
eleven (including `reserved-activity-review-school`'s own `ReservedBlock`
count of exactly 5, matching its already-closed A2 record); no write was
ever directed at any of them; never claimed as byte-for-byte/row-by-row.

Full regression reconfirmed after the corrective mobile-nav fix
(current baseline, superseding this session's pre-fix numbers): core
398 passed/5 deselected, `tests_web` 396 passed/zero skips,
**frontend 403 passed/25 files/zero skips** (402 + one new
`App.test.tsx` nav-CSS-hook regression test -- not 403 "new tests"),
build clean, Alembic `cae76cba3c58`/one head/no drift. Zero production/
test/frontend file changed by the acceptance steps themselves (the
only local change those steps made, `frontend/.env.local`'s gitignored
school/year override, was restored to `synthetic-school`/`ay-2026`
after each use); the two-file mobile-nav fix is tracked separately as
its own commit, `865138d`, and is the one production change this
closure required.

**Owner Decision #39 remains NOT created** -- the 3-teacher dataset
shape was a test-fixture engineering decision, never a product fork.

**Closure history, corrected forward, not erased:** `072aea7`
recorded the *first* phase closure; subsequent review found its
narrow-acceptance evidence applied Reserved C's "no material
page-level horizontal overflow" criterion too permissively (the 93px
`AppShell` nav overflow was waved through as pre-existing/out-of-scope
rather than treated as the blocker it was). Fix commit `865138d`
closed that one remaining defect; no other Reserved C evidence was
ever invalid or repeated. With the gap corrected and re-verified at a
genuine <=640px viewport, **the `072aea7` phase closure is
retroactively valid.**

**RESERVED ACTIVITIES PHASE CLOSED.** A1, A2, B, and C are all closed/
passed. Shipped: Special Activity catalog; fixed Reserved Activity
aggregate (Special Activity + 1+ Classes + optional single Teacher +
explicit instructional slots); hard Teacher-UNAVAILABLE rejection with
non-blocking PREFER_NOT; cross-block collision safety; fixed
zero-CP-SAT-variable solver occupancy; correct Class/Teacher timetable
projection (teacher-attached and teacherless); configuration-lock
enforcement across both UI surfaces and the raw API; a responsive,
accessible frontend workflow. Deferred: Resources, ParticipantGroups/
subgroups in Reserved Activities, multiple Teachers per block,
recurrence, duration semantics, flexible/autoplaced special activities,
`ReservedBlock` soft solver scoring.

## RESOURCES A -- RESOURCE CATALOG BACKEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `6a90a28` ("feat: add
resource catalog backend") was fast-forward merged onto `main` (from
`feature/resource-catalog-backend`, base `976b646`); the docs closure
commit recording this status follows as its own separate commit. The
feature branch has been deleted. Nothing has been pushed to any
remote. **The overall Resources phase itself is NOT closed** -- this
entry closes Slice A (the catalog backend) only; see "Next slice"
below.

Preceded by a read-only recon (concluded "A. RESOURCES RECON COMPLETE
-- PRODUCT DECISIONS REQUIRED") that mapped pre-existing Resource
support across the domain/solver/preflight/verifier/persistence/API-
read layers and confirmed the live PostgreSQL `resource` table already
matches the target schema exactly -- Slice A required zero migration.

Scope: the narrow Resource catalog write surface (`GET/POST
/schools/{school_id}/years/{year_id}/resources`, `PUT/DELETE
.../resources/{resource_id}`) over the existing, unchanged
`domain.resources.Resource(id, name, capacity=1)` entity -- no new
Room/ResourceType/ResourceCategory entity, no domain redesign. (A
future frontend surface for this catalog is expected to be labeled
"Rooms & Resources" in the UI -- a presentation-layer naming decision
only; the domain/API entity/field names above are unaffected and
remain exactly as shipped.)
`capacity` means "maximum number of simultaneous lesson/resource
occupations" (already the pre-existing solver/verifier semantics via
`model_builder.py::_add_resource_capacity()`), explicitly never
student-seat/room-headcount capacity. Validated `>= 1` at the
application layer (`resource_rules.validate_capacity`,
`INVALID_RESOURCE_CAPACITY`), never relying on the DB
`CheckConstraint("capacity > 0")` alone (that constraint remains a
structural backstop only).

Follows every existing catalog precedent (Teacher/Class/Subject/
Special Activity) exactly: server-generated `resource_<uuid4().hex>`
natural IDs; trimmed, exact case-sensitive duplicate-name check scoped
solely to the Resource catalog (an identically-named Subject/Teacher/
Class/Special Activity is never a conflict); Resource owns its own
ordinal sequence, never shared with any other catalog; full
configuration-lock discipline (409 `SCHEDULING_CONFIGURATION_LOCKED`,
reusing `configuration_write_lock.py` unchanged); Owner-Decision-#36
generation-race safety (reusing the existing exact-equality stale-input
detection on `SchedulingProblem`, since `resources` was already an
ordinary field on it) -- proven with a dedicated race test pair
(`test_generation_persist_aborts_when_resource_write_committed_since_load`,
`test_generation_persist_succeeds_then_blocks_a_waiting_resource_write`)
mirroring the Special Activity precedent exactly.

Delete-in-use blocker checks only `TeachingRequirement.resource_id`
(`RESOURCE_IN_USE`, `referenced_by: ["TEACHING_REQUIREMENT"]`) --
deliberately NOT `RESERVED_BLOCK` yet, since `ReservedBlock.resource_id`
does not exist in Slice A. `/config`'s pre-existing read-only
`ResourceResponse`/`ResourceRequirementResponse` schemas were left
untouched; new schema names (`ResourceProjectionItemResponse`,
`ResourcesProjectionResponse`, `ResourceWriteRequest`/`Response`,
`ResourceDeleteResponse`, and three error-response schemas) were added
instead to avoid collision. `/config` confirmed to reflect Resource
create/update/delete immediately (dedicated interoperability tests).

**Preflight capacity-validation gap -- discovered during Resources A
implementation review, resolved before closure.** A hand-constructed
`SchedulingProblem` with `Resource(capacity<=0)` was confirmed to pass
`run_preflight()` with zero errors -- preflight previously had no
capacity check at all (only `UNKNOWN_RESOURCE` for a dangling
reference). This was a real, confirmed validation hole; per the
original slice instruction it was first reported rather than silently
fixed, and a subsequent narrow corrective pre-closure pass then
resolved it: `validation/preflight.py` gained a new, independent
`_check_resource_capacity()` check, and `run_preflight()` now emits a
`ValidationError` with code `INVALID_RESOURCE_CAPACITY` (context
`{"resource_id", "capacity"}`) for every `Resource` in
`problem.resources` with `capacity < 1`, one diagnostic per invalid
Resource in the problem's own authoritative order. The check is purely
structural (`domain`-only, no `application`/SQLAlchemy/ORM/FastAPI
import) and independent of `_check_references`/`UNKNOWN_RESOURCE`
(proven by a dedicated non-interference test) -- it runs regardless of
whether other reference errors are present. The application-layer
`resource_rules.validate_capacity()` write-time rejection and the DB
`CheckConstraint("capacity > 0")` structural backstop both remain
unchanged; the three layers (write-time application validation,
`SchedulingProblem` preflight, DB `CHECK`) are each still independently
necessary and now all three are proven to work. Zero solver or verifier
changes were needed or made -- preflight now rejects invalid Resource
capacity before the solver ever runs, and the verifier's own
`_check_resource_capacity()` (an unrelated, pre-existing, differently-
named function checking final generated-schedule resource usage, not
this one) is untouched. 7 new focused tests added to
`tests/test_preflight.py` (capacity 0/negative/1/>1, multi-invalid
deterministic ordering, `UNKNOWN_RESOURCE` non-interference, and a
fixture-level regression guard), all passing.

**Corrected forward guidance for a future Resources B (Reserved
Activity resource integration):** any future work wiring `Resource`
into `ReservedBlock` must enforce **aggregate Resource capacity**
(a resource's total concurrent-occupation count across every slot,
the same semantics `model_builder.py`'s existing HARD constraint #10
already uses for ordinary `TeachingRequirement` resource use) -- never
a pairwise `RESERVED_BLOCK_RESOURCE_SLOT_COLLISION`-style two-block
exclusivity check. A pairwise check would silently under- or
over-constrain any Resource whose `capacity != 1`.

Explicitly deferred/out of scope for this slice: all frontend changes
(zero -- Resources has no UI surface yet); `ReservedBlock.resource_id`
(does not exist); Resource Availability; any write path from
`TeachingRequirement` to a `resource_id` (TeachingRequirement's own
`resource_requirement` remains read-only/deferred, per its own existing
deferred-scope note); Owner Decision #39 (absent, not created);
solver/verifier/migration changes (none needed).

Tests added: 29 pure-application (`tests/test_resource_service.py`), 15
repository/integration including the mandatory generation-race pair
(`tests_web/test_resource_repository.py`), 27 HTTP contract
(`tests_web/test_resource_api.py`), plus 7 focused preflight tests
added during the pre-closure corrective pass (`tests/test_preflight.py`)
-- 78 new tests total, all shipped in implementation commit `6a90a28`.

**Final verified baselines, reconfirmed after the fast-forward merge to
`main`:** core **434 passed / 5 deselected**; `tests_web` **438 passed /
zero skips**; frontend **403 passed / 25 files / zero skips**; build
**clean**; Alembic **`cae76cba3c58` / one head / no drift** (zero
migration). Scope audit confirmed the implementation commit touched
only backend Resources A files (`application/`, `persistence/`, `api/`,
`tests/`, `tests_web/`) plus the corrective
`validation/preflight.py`/`tests/test_preflight.py` change, plus the
two pending-review docs entries carried into that same commit -- zero
frontend/Alembic/domain-redesign/solver/verifier/ReservedBlock/
dependency/`uv.lock`/`dist` changes.

**Final shipped contract (Resources A):**
- Existing `domain.resources.Resource(id, name, capacity)` reused unchanged -- no new entity, no domain redesign.
- Future UI terminology for this catalog: "Rooms & Resources" (presentation-layer only).
- `capacity` = maximum simultaneous resource occupations (never student-seat/room-headcount capacity); validated `>= 1`.
- Server-generated `resource_<uuid4().hex>` natural IDs, never client-supplied.
- Exact, trimmed, case-sensitive duplicate-name semantics, scoped solely to the Resource catalog.
- Resource owns its own ordinal sequence, never shared with any other catalog.
- `GET/POST /schools/{school_id}/years/{year_id}/resources`, `PUT/DELETE .../resources/{resource_id}` -- full catalog CRUD API.
- `RESOURCE_IN_USE` delete blocker via `TeachingRequirement.resource_id` only.
- Configuration-lock enforcement (409 `SCHEDULING_CONFIGURATION_LOCKED`), reusing existing infrastructure unchanged.
- Owner-Decision-#36 generation-race safety, proven with a dedicated race test pair.
- `/config` compatibility -- pre-existing read-only schemas untouched, Resource CRUD reflected immediately.
- Preflight `INVALID_RESOURCE_CAPACITY` diagnostic (added in the pre-closure corrective pass).
- Layered validation, all three now proven independently: application write-time validation + `SchedulingProblem` preflight + DB `CHECK` constraint.
- Zero migration; zero solver change; zero verifier change; zero frontend change.
- No `ReservedBlock.resource_id` yet.
- No Resource Availability yet.
- No ordinary `TeachingRequirement` resource-write surface yet (`resource_requirement` remains read-only/deferred).
- Corrected forward guidance, locked: any future Reserved Activity resource integration must use **aggregate Resource capacity**, never pairwise `RESERVED_BLOCK_RESOURCE_SLOT_COLLISION`-style two-block exclusivity semantics.
- Owner Decision #39 remains absent -- not created by this slice.

**RESOURCES A -- RESOURCE CATALOG BACKEND CLOSED ON MAIN.** Implementation
commit `6a90a28`. The overall Resources phase is **NOT** yet closed.

## RESOURCES B1 -- ORDINARY FIXED-RESOURCE ASSIGNMENT CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `0a61339` ("feat: add
resource assignment to teaching requirements") -- fast-forward merged
from `feature/resource-assignment-b1` (base `830ce06`) onto `main`,
with a separate docs closure commit recording this status. Nothing has
been pushed to any remote. **The overall Resources phase remains NOT
closed** -- this entry closes Slice B1 only.

**Final verified baselines (reconfirmed on `main` after the fast-forward
merge):** core 446 passed/5 deselected, `tests_web` 464 passed/zero
skips, frontend 415 passed/25 files/zero skips (one unrelated
pre-existing `TimetablePage.test.tsx` timing test flaked once under
full-suite load and passed cleanly on immediate isolated rerun and a
second full-suite run -- confirmed not a regression, nothing in this
slice touches that page), build clean, Alembic `cae76cba3c58`/one
head/no drift/zero migration. Post-merge minimal reconfirmation: 108
focused backend Teaching Assignment tests and 96 focused frontend tests
passed on `main`, working tree clean, build clean.

**Real-browser functional check result:** confirmed live against the
existing, unlocked `teacher-crud-review-school`/`ay-teacher-crud-2026`
dataset (`.env.local` restored afterward) -- previously-stranded
resource-bearing rows (`sport_*`/`dance_*`, using "Indoor Gym") now show
"Standard" with active Edit/Delete instead of "Advanced"/"Read-only";
assign/reopen-confirm/clear/reopen-confirm/reassign all worked correctly
across four save cycles with no stale or error state.

**Next slice: Resources B2 -- Reserved Activity Resource integration
using aggregate Resource capacity** (never pairwise
`RESERVED_BLOCK_RESOURCE_SLOT_COLLISION`-style two-block exclusivity,
per the corrected forward guidance locked in the Resources A entry
above).

---

**Implementation record (Option A contract, as shipped):**

**Locked product contract (Option A, per the B1 recon's recommendation):**
the existing Teaching Assignment `POST/PUT .../teaching-assignments[/{id}]`
contract gained one new field, `resource_id: str | null`, rather than a
second endpoint or a separate Advanced editor. Full-replacement
semantics, matching every other field on this write: POST omitted/null
`resource_id` means "no fixed Resource"; PUT omitted `resource_id` OR
explicit `null` both mean "clear any Resource currently assigned" --
there is no partial-update "leave unchanged" option; a non-null value
assigns/replaces that exact Resource. Mapped 1:1 onto the existing,
unchanged `domain.resources.ResourceRequirement(resource_id)` --
`resource_id=None` -> `TeachingRequirement.resource_requirement=None`,
otherwise `ResourceRequirement(resource_id=resource_id)`.

**The Advanced/read-only gate corrected.** `teaching_assignment_rules.
plain_reasons()` no longer treats a non-null `resource_requirement` as
an Advanced disqualifier on its own -- an otherwise-plain
(`WHOLE_CLASS`, `FLEXIBLE` block policy, no split group, no time
preferences, no `FixedPlacement`) resource-bearing requirement is now
editable and deletable through this same narrow write service. Every
other existing Advanced reason (non-whole-class target, split group,
non-`FLEXIBLE` block policy, non-default distribution policy, time
preferences, `FixedPlacement`) is completely unchanged. In the shipped
`valid_fixture`, this immediately unblocks all 8 previously-stranded
`sport_*`/`dance_*` rows (the gym-using rows) -- confirmed live via a
real-browser pass (see below).

**Resource reference validation** reuses `resource_rules.find_resource`
(Resources Slice A) via a new `require_known_resource()` helper --
raises the existing, generic `UnknownReferenceError(reference_kind=
"resource", reference_id=...)` (422 `UNKNOWN_REFERENCE`) for an unknown
or cross-AY Resource natural ID, mirroring the identical treatment
already given to `teacher_id`/`activity_id`/`participant_group_id`. No
new error class was introduced.

**Projection additions:** `TeachingAssignmentItem`/`TeachingAssignmentResponse`
gained `resource_id: str | null`; `TeachingAssignmentsProjectionView`/
`TeachingAssignmentsProjectionResponse` gained a `resources: [{id, name,
capacity}]` option list (in the Resource catalog's own authoritative
order), matching the existing `activities`/`teachers`/`whole_class_targets`
option-list pattern exactly. No cross-projection-module type reuse --
the small `TeachingAssignmentResourceOption`/`TeachingAssignmentResourceOptionResponse`
shapes are local to this module, matching how `TeacherOption`/`ActivityOption`
are already local rather than imported from other projection modules.

**Frontend (`AssignmentDrawer`/`TeachingAssignmentsPage`):** one new
`<select>` field, "Resource," in the existing single create/edit drawer
-- "No resource" is itself a first-class selectable option (not a
"please choose" placeholder), and the frontend always submits
`resource_id` explicitly as `string | null`, never omitted, keeping the
wire contract unambiguous even though the backend also treats omission
as null. The Activity table cell gained a small muted subtext line
showing the assigned Resource's name (or "No resource") -- deliberately
not a new table column. A `resource_id` present on a row but absent
from the current `resources` option list renders as a safe "Unknown
resource" (never a raw ID) and blocks Edit for that row only (mirroring
the existing `whole_class_targets` orphan-row precedent) -- Delete is
never affected by either mapping.

**Atomicity/locking/race safety:** unchanged infrastructure reused
verbatim -- one write, one `AcademicYear` row lock, one `validate`
closure, one commit (Owner Decision #36); `resource_id` is threaded
through the *same* single transaction as every other field, never a
second request. A dedicated race test
(`test_generation_persist_aborts_when_only_a_resource_assignment_changed`)
proves a write that changes *only* `resource_id` (every other field
identical) still triggers the existing stale-input abort, confirming
`TeachingRequirement.resource_requirement` genuinely participates in
`SchedulingProblem`'s frozen-dataclass equality Owner Decision #36
already relies on.

**Zero solver, verifier, domain, or schema change.** `Resource`,
`ResourceRequirement`, `TeachingRequirement.resource_requirement`, the
`teaching_requirement.resource_id` column, the solver's resource-capacity
constraint, and preflight's `UNKNOWN_RESOURCE`/`INVALID_RESOURCE_CAPACITY`
checks were all already fully wired (Resources Slice A) and required no
changes -- confirmed by rerunning the existing solver/verifier
resource-capacity fixture tests (`test_editing_moves.py::test_resource_capacity_conflict_rejected`,
`test_verifier.py`/`test_preflight.py`'s resource-scoped tests), all
still passing unmodified.

**Explicitly deferred/out of scope for B1:** Resource Availability
(unchanged, still deferred); `ReservedBlock.resource_id` (does not
exist -- that is Resources B2); the solver never chooses among
Resources (a fixed, admin-picked Resource only); Owner Decision #39
remains absent (no genuine unresolved product fork was found in the B1
recon).

**Real-browser functional check** against the existing, unlocked
`teacher-crud-review-school`/`ay-teacher-crud-2026` local dataset
(`frontend/.env.local` temporarily pointed there, then restored to
`synthetic-school`/`ay-2026` afterward, matching established practice):
confirmed the previously-stranded "Teacher Sport / 8-A / Sport / Indoor
Gym" and "Teacher Dance / 8-A / Dance / Indoor Gym" rows now show
"Standard" (not "Advanced") with active Edit/Delete; assigned "Indoor
Gym" to a previously-resource-free assignment ("Teacher Science / 8-A /
Science") via the edit drawer, confirmed the row updated and the
Resource re-selected correctly on reopen, cleared it back to "No
resource," confirmed the row updated, then reassigned it -- all four
save cycles reflected correctly with no stale/error state.

**Tests added:** 12 pure-application (`tests/test_teaching_assignment_service.py`,
covering create/update with omitted/null/valid/unknown/cross-snapshot
resource references, clearing, reassigning, otherwise-plain
editability/deletability, and other-Advanced-reasons-unaffected), 9
repository/integration plus 1 dedicated generation-race proof
(`tests_web/test_teaching_assignment_repository.py`), 16 HTTP contract
(`tests_web/test_teaching_assignment_api.py`, covering GET shape/
editability/resource-options, POST/PUT create/clear/reassign/unknown/
cross-AY, and DELETE for an otherwise-plain resource-bearing row) -- 38
new backend tests, plus 12 new frontend tests
(`TeachingAssignmentsPage.test.tsx`) covering the Resource select
render/empty-catalog/create/change/clear/row-display/unknown-fallback/
locked-disable behavior. Two small pre-existing tests were corrected to
reflect the new, intentional behavior (the "resource_requirement is
Advanced" assertion became "otherwise-plain resource-bearing
requirement is editable"); two pre-existing exact-shape assertions
(`test_get_projection_full_contract_shape`'s key sets) were extended
with the new fields.

**Full regression:** core 446 passed/5 deselected (434 + 12 new),
`tests_web` 464 passed/zero skips (438 + 17 + 9 new... i.e. 438 + 26
new: 9 repository + 1 race + 16 API), frontend 415 passed/25 files/zero
skips (403 + 12 new; one unrelated pre-existing `TimetablePage.test.tsx`
timing test flaked once under full-suite load and passed cleanly on
immediate rerun in isolation and in a second full-suite run -- not a
regression, nothing in this slice touches that page), build clean,
Alembic `cae76cba3c58`/one head/no drift/zero migration. Scope audit
confirmed only the expected Teaching-Assignment-related backend/
frontend/test files changed -- zero domain/solver/verifier/migration/
dependency/ReservedBlock/Resource-catalog-backend changes.

## RESOURCES B2 -- RESERVED ACTIVITY RESOURCE INTEGRATION CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `e8a3aaf` ("feat: add
resources to reserved activities") -- fast-forward merged from
`feature/reserved-activity-resource-b2` (base `4025376`) onto `main`,
with a separate docs closure commit recording this status. Nothing has
been pushed to any remote. This closes Slice B2 only.

**Final verified baselines (reconfirmed on `main` after the fast-forward
merge):** core 479 passed/5 deselected, `tests_web` 486 passed/zero
skips, frontend 428 passed/25 files/zero skips, build clean, Alembic
`9fbec2126831`/one head/no drift on both the primary and
`TEST_DATABASE_URL` databases. Post-merge minimal reconfirmation: 300
focused backend tests (application/preflight/solver/verifier/
repository/API, across Resources A/B1/B2) and 84 focused frontend tests
passed on `main`, working tree clean, build clean.

**Resources phase status: MVP scope (Resources A + B1 + B2) is now
functionally complete** -- Resource catalog CRUD, ordinary Teaching
Assignment fixed-resource assignment, and Reserved Activity
fixed-resource assignment with a fully proven aggregate (never
pairwise) cross-source capacity invariant are all shipped and closed on
`main`. **Formal closure of the overall Resources phase is
deliberately left to the next product step, not declared here** -- no
prior record defines B2 as the final required slice, and Resource
Availability remains an explicitly deferred, not-yet-scoped future
concern (never treated as a completeness blocker, per its own repeated
"still deferred" notes above).

**Next slice (if the product proceeds further on Resources):** Resource
Availability, if and when it is prioritized -- no Owner Decision or
implementation groundwork for it exists yet.

**Contract:** `ReservedBlock` gained one new optional field,
`resource_id: str | None`, exposed on the Reserved Activity API as
`resource_id` (POST/PUT/GET, full-replacement like every other field --
omitted/null clears; a non-null value assigns/replaces). At most one
fixed Resource per `ReservedBlock`, never solver-selected. **One
occupation unit per block per slot, regardless of participant-class
count:** a `ReservedBlock` spanning N classes still consumes exactly
ONE capacity unit of its Resource in each of its own `slots` -- never
N units. This is encoded once, centrally, in a new
`ProblemIndex.reserved_resource_usage: dict[(resource_id, day_id,
period_id), int]` index (one entry per distinct `ReservedBlock`,
counted once regardless of `class_sections` length), reused by
preflight, the solver, and (implicitly, via `ScheduleEntry.resource_id`)
the independent verifier -- so the "one block, one unit" rule can never
silently diverge between layers.

**Aggregate capacity, never pairwise collision -- proven at three
independent layers:**
1. **Preflight** (`_check_reserved_block_resource_capacity`, new): a
   purely structural, reserved-vs-reserved-only check -- for each
   Resource and slot, the count of distinct `ReservedBlock`s fixed
   there must not exceed `Resource.capacity`. Emits
   `INVALID_RESOURCE_CAPACITY`... `RESERVED_RESOURCE_CAPACITY_EXCEEDED`
   (context: `resource_id`, `day_id`, `period_id`, `capacity`,
   `reserved_usage`), one diagnostic per over-capacity slot, in
   `problem.resources` order then `(Day.index, Period.index)` order.
   Reused by `reserved_activity_rules`'s existing candidate-diff
   mechanism (added to `_RESERVED_ACTIVITY_BLOCKING_CODES`) for both
   the service's fast precheck and the repository's authoritative,
   lock-protected recheck -- zero new validation architecture. A new
   `UNKNOWN_RESOURCE` check for `ReservedBlock.resource_id` was added
   alongside it (mirroring the existing `TeachingRequirement` one).
2. **Solver** (`model_builder._add_resource_capacity`, extended): fixed
   `ReservedBlock` usage now pre-consumes capacity before ordinary
   lesson variables are constrained --
   `available = max(0, capacity - reserved_fixed_usage)`, then
   `ordinary_scheduled_usage <= available` per slot. `ReservedBlock`s
   remain fixed input, never CP-SAT decision variables. Proven with
   three focused solver tests
   (`tests/test_reserved_block_resource_solver.py`): Case A (capacity
   exhausted by a Reserved Activity forces an ordinary lesson to a
   different slot), Case B (remaining capacity, `capacity=2`, is
   genuinely still usable -- proving this is NOT a pairwise "any use
   blocks the slot" rule), Case C (two conflicting fixed Reserved
   Activities are caught by preflight, confirmed the solver is never
   the only layer able to catch this, since `ReservedBlock`s are never
   modeled as decision variables at all).
3. **Independent verifier**: `verifier._check_resource_capacity`
   already counted every final `ScheduleEntry` by `resource_id`
   regardless of `source` -- **zero verifier production change was
   needed**, only `ScheduleEntry.resource_id` needed to be populated
   correctly for `RESERVED_BLOCK`-sourced entries (see the correctness
   fix below). Proven with two new verifier tests covering a forged
   cross-source (one `RESERVED_BLOCK` + one ordinary) capacity
   violation, and a legal mixed-use case within capacity.

**A real correctness defect was found and fixed in this same task**
(per the task's own instruction to fix such issues here rather than
opening a new gate cycle): `scheduling/result_builder.py`'s freshly-
solved `ScheduleEntry` construction was updated to carry
`resource_id=block.resource_id` for `RESERVED_BLOCK` entries, but the
**separate** read-back mapper, `persistence/mappers.py::schedule_entry_to_domain`
(reconstructs a domain `ScheduleEntry` from an already-*persisted*
`schedule_entry` row by joining back to `reserved_block`/
`teaching_requirement` -- used by `get_active_schedule` and every
timetable projection that reads a stored schedule) -- was missed on
the first pass. This was caught by a live, real-browser-driven
`POST .../schedule/generate` check on the resource-assigned dataset:
the freshly-solved schedule's own cross-source capacity was already
correct (the solver used the live, correctly-mapped `SchedulingProblem`),
but the *persisted-then-reloaded* schedule showed `resource_id: None`
on the `RESERVED_BLOCK` entry. Fixed with a one-line addition
(`resource_id=block.resource_id`) to that mapper's `RESERVED_BLOCK`
branch. Regression-proven at two levels: a new
`tests_web/test_schedule_repository.py` round-trip test
(`test_persist_then_read_back_preserves_reserved_block_resource_id` --
`build_valid_fixture()`'s own two `ReservedBlock`s never carry a
Resource by default, so the pre-existing broader round-trip test could
not have caught this) and two new real-solver, real-persistence
timetable API tests proving the Resource is visible end-to-end in both
the Class and Teacher timetable projections
(`test_real_reserved_block_resource_visible_in_class_timetable`,
`test_real_reserved_block_resource_visible_alongside_teacher`) --
`ClassTimetableService`/`TeacherTimetableService` needed zero changes,
since both already read the generic `ScheduleEntry.resource_id` field.

**Persistence:** one narrow Alembic migration, `9fbec2126831` ("add
reserved_block resource_id"), adding a nullable
`reserved_block.resource_id` `BigInteger` column plus a composite FK
`(academic_year_id, resource_id) -> resource(academic_year_id, id)`
`ON DELETE RESTRICT`, mirroring `teaching_requirement.resource_id`'s
existing pattern exactly. Applied to both the primary and the
`TEST_DATABASE_URL` PostgreSQL databases. All 22 existing
`reserved_block` rows survived the migration with `resource_id = NULL`
(confirmed by direct query before regression). Alembic
`9fbec2126831`/one head/no drift on both databases.

**Resource delete-in-use blocker extended:**
`resource_rules.find_resource_references` gained a `RESERVED_BLOCK`
branch (after the existing `TEACHING_REQUIREMENT` one, deterministic
order preserved) -- a Resource referenced by either kind now blocks
delete with `RESOURCE_IN_USE`,
`referenced_by: ["TEACHING_REQUIREMENT", "RESERVED_BLOCK"]` (both, in
that order, when both apply). No cascade-delete, no silent clearing.

**Frontend:** `ReservedActivityEditor` gained one new "Resource"
`<select>` (mirroring the existing "Teacher" select's exact optional-
field pattern -- "No resource" as a first-class option, never a
placeholder), threaded through `ReservedActivityDraftValues.resourceId`
and included in the dirty-check/`isStructurallyValid` logic exactly
like every other field. `ReservedActivityCard` now displays the
assigned Resource's name (or "No resource", or a safe "Unknown
resource" fallback that also blocks Edit but never Delete, mirroring
the existing unresolvable-teacher/class-section precedent exactly).
`ReservedActivitiesPage` gained a `RESERVED_RESOURCE_CAPACITY_EXCEEDED`
diagnostic-to-friendly-message mapping (resolves the Resource name and
slot, e.g. "Gym is already fully booked at Monday P1 (capacity 1).").
Resources catalog is never a prerequisite for creating a Reserved
Activity -- an empty catalog simply offers only "No resource".

**Real-browser functional check:** confirmed live against the existing,
unlocked `teacher-crud-review-school`/`ay-teacher-crud-2026` dataset
(`.env.local` restored afterward) -- assigned "Indoor Gym" to "Chess
Club" via the editor, confirmed the card updated; reopened Edit,
confirmed "Indoor Gym" preselected; cleared to "No resource", confirmed
the card updated; reassigned it. All four save cycles reflected
correctly with no stale/error state. A real `POST .../schedule/generate`
against this same resource-assigned dataset (25 ordinary
`TeachingRequirement`s, 8 of them gym-using Sport/Dance rows, plus the
newly gym-fixed Chess Club reserved block) confirmed OPTIMAL status
with the aggregate constraint holding throughout: zero ordinary gym
usage was placed at the reserved block's own (Wednesday, Period 8)
slot, and gym usage never exceeded its capacity=1 anywhere in the
final 160-entry schedule -- this generation run is what surfaced the
mapper defect above. The generated `Schedule` was deleted afterward to
restore the shared review dataset's unlocked state, matching this
session's established practice for reusable review fixtures (the
`.env.local` restoration and the Resource assignment left on the
dataset both mirror Resources B1's identical precedent).

**Explicitly deferred/out of scope for B2:** Resource Availability
(unchanged, still deferred); eligible-Resource sets, Resource
categories/capabilities, preferred Resource (none of these exist,
matching the locked B2 contract); student-seat/headcount semantics
(never introduced); the solver never chooses among Resources. Owner
Decision #39 remains absent -- no genuine unresolved product fork
appeared during this task.

**Tests added:** 11 preflight (`tests/test_preflight.py`), 3 solver
(new `tests/test_reserved_block_resource_solver.py`), 2 verifier
(`tests/test_verifier.py`), 2 Resources-A delete-blocker
(`tests/test_resource_service.py`), 17 pure-application
(`tests/test_reserved_activity_service.py`), 11 repository/integration
(`tests_web/test_reserved_activity_repository.py`, including the
aggregate-capacity-exceeded-leaves-no-partial-mutation and
generation-race proofs), 1 Resources-A repository delete-blocker
(`tests_web/test_resource_repository.py`), API tests in
`tests_web/test_reserved_activity_api.py` (3 pre-existing exact-shape
assertions corrected for the new fields, plus new resource-option/
create/clear/reassign/unknown/aggregate-capacity/config-visibility
tests), 1 schedule-repository round-trip regression
(`tests_web/test_schedule_repository.py`), 1 Class Timetable and 1
Teacher Timetable real-solver API test -- plus 13 new frontend tests
across `ReservedActivityCard.test.tsx` (5), `ReservedActivityEditor.test.tsx`
(7), and `ReservedActivitiesPage.test.tsx` (1), with existing fixtures/
assertions across five frontend test files updated for the new
`resource_id`/`resources` fields.

**Full regression:** core 479 passed/5 deselected (446 + 33 new),
`tests_web` 486 passed/zero skips (464 + 22 new), frontend 428
passed/25 files/zero skips (415 + 13 new), build clean, Alembic
`9fbec2126831`/one head/no drift on both the primary and test
databases. Scope audit confirmed only the expected Reserved-Activity-
/Resource-integration-related domain/persistence/application/api/
scheduling/validation/frontend/test files changed, plus the one new
migration file -- zero unrelated Resources-A/Teaching-Assignment/
dependency changes.

## RESOURCES C -- ROOMS & RESOURCES CATALOG FRONTEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `58e8b5c` ("feat: add
rooms and resources setup UI") -- fast-forward merged from
`feature/resources-catalog-frontend` (base `ec8a238`) onto `main`, with
a separate docs closure commit recording this status. Nothing has been
pushed to any remote. This closes Slice C -- a frontend-only slice, with
**zero backend/migration/solver/verifier/domain production changes**.

**Doc correction (forward-only, not a rewrite of the B2 entry above):**
the RESOURCES B2 entry's "Resources MVP scope (A + B1 + B2) is now
functionally complete" statement described the *backend/scheduling/
resource-assignment* behavior only -- it did not yet ship any
user-facing way for a school administrator to create, edit, or delete a
Resource without API fixtures or database tooling. That gap is what
this slice closes. Precisely: **A + B1 + B2 completed the backend,
scheduling, and resource-assignment behavior; Resources C closes the
missing user-facing catalog-management surface; Resource Availability
remains explicitly deferred and was never an MVP blocker.** With this
slice closed, the Resources MVP phase itself is now formally closed
(see the declaration at the end of this entry).

**Product/UI contract:** the internal/domain/API entity remains
`Resource`; its user-facing label is **"Rooms & Resources"**, added as
a fifth tab on the existing School Setup page (`SchoolSetupPage.tsx`),
after Special Activities, in the exact same self-contained-panel
architecture as the other four tabs (own independent GET on mount, no
cross-tab cache, no change to the tab mechanism itself). Only the two
already-shipped Resource fields are exposed -- `name` and `capacity` --
never a natural ID, ordinal, or database surrogate ID. Capacity is
presented to the admin as **"the maximum number of simultaneous uses"**
(capacity 1: only one lesson/activity may use this Resource at a time;
capacity 2: two simultaneous uses are allowed), never seat/headcount/
room-occupancy language -- a small, visually-distinct tinted callout
(`.capacity-hint`, deliberately different from the plain muted
`.field-hint` text used elsewhere, so it reads as a separate explanatory
note rather than blending into the editable field) carries this
explanation next to the Capacity input on both the create form and the
inline row-edit form. New Resources default to capacity 1; the capacity
input is `type="number" min="1" step="1"`, with local validation
(integer, >= 1) blocking submission before any request is sent --
backend validation (`INVALID_RESOURCE_CAPACITY`, "capacity must be at
least 1") remains the authoritative backstop and its message is still
surfaced verbatim if ever reached.

**Frontend API module:** `frontend/src/api/resources.ts`, a new,
dedicated module following the `specialActivities.ts` "one module per
page-domain" convention exactly (`getResources`/`createResource`/
`updateResource`/`deleteResource`, thin one-line wrappers around
`client.ts`'s shared `getJson`/`postJson`/`putJson`/`deleteJson`) --
against the existing, unmodified Resources A backend endpoints
(`GET/POST /schools/{school_id}/years/{year_id}/resources`,
`PUT/DELETE .../resources/{resource_id}`). No generic CRUD engine was
introduced.

**Create/Edit/Delete:** `RoomsResourcesPanel.tsx` mirrors
`SpecialActivitiesPanel.tsx`'s architecture precisely -- collapsible
inline create form, row-inline edit, inline (non-modal, no
`window.confirm`) delete confirmation, and an authoritative GET refetch
after every successful write (never an optimistic local merge). Create
and update both send the whole `{name, capacity}` representation
(full-replacement, matching every other write endpoint in this
codebase); no IDs are ever rendered.

**Delete-in-use UX:** `RESOURCE_IN_USE` is mapped to a friendly message
using the same `referenced_by`-to-label pattern already established by
`SpecialActivitiesPanel` -- `TEACHING_REQUIREMENT` renders as "Teaching
Assignments", `RESERVED_BLOCK` renders as "Reserved Activities"; when
both apply, both labels are listed ("...used by: Teaching Assignments,
Reserved Activities."). Raw backend vocabulary is never rendered to the
user. No cascade or silent clearing -- delete is simply blocked with the
friendly message, matching the backend's own `ResourceInUseError`
semantics exactly (no backend change was needed here).

**Configuration lock UX:** reuses the existing shared
`configuration_locked`/lock-banner pattern verbatim -- when locked, the
catalog remains fully visible, Add/Edit/Delete are disabled (not merely
hidden, and not conveyed by color alone -- a real `disabled` attribute
plus the existing lock banner text), and a write racing a fresh lock
(`SCHEDULING_CONFIGURATION_LOCKED`) refetches into the now-locked
authoritative state, matching every other School Setup tab's existing
race-handling precedent. No Resource-specific locking behavior was
invented.

**Real-browser CRUD result:** confirmed live against the existing,
unlocked `teacher-crud-review-school`/`ay-teacher-crud-2026` dataset
(`.env.local` restored afterward): opened School Setup -> Rooms &
Resources, confirmed the pre-existing "Indoor Gym" (capacity 1) Resource
was visible; created "Science Lab" (capacity 1, the default), confirmed
it appeared from the authoritative refresh; edited it to "Science Lab
A" with capacity 2, confirmed both fields updated from the authoritative
refresh; deleted it, confirmed it was removed. Then attempted to delete
"Indoor Gym" itself, which this dataset's existing configuration
references from **both** a Teaching Assignment (`sport_8a`/`dance_8a`/
etc.) and a Reserved Activity (`club_chess`) -- confirmed the delete was
blocked with "This resource can't be deleted because it is used by:
Teaching Assignments, Reserved Activities." and cancelled the
confirmation, leaving the dataset's shared fixture state untouched (only
the newly-created-and-deleted "Science Lab A" ever left a trace, and it
left none once deleted -- the dataset ends this task in the identical
state it started, confirmed by direct query: exactly one Resource,
"Indoor Gym"/capacity 1, zero `schedule_version` rows).

**Narrow viewport result (~375px):** the Rooms & Resources tab, its
create form, and its row-edit form all reflow correctly at 375px width
(stacked labels/full-width inputs, matching the existing narrow-width
`setup-table` card-stacking convention already shared by every School
Setup tab). One real, narrow-width-only defect was found and fixed in
this same task: the five-tab `.setup-tablist` (a plain flex row with no
wrap/scroll handling) overflowed the page horizontally once a fifth tab
was added -- confirmed via direct DOM measurement
(`document.documentElement.scrollWidth` exceeding `clientWidth`) inside
a same-origin iframe probe sized to 375px (the sandboxed browser
environment's OS-level window could not itself be resized below its
fixed display size, so this in-page iframe technique was used instead
of a real window resize). Fixed by giving `.setup-tablist` its own
contained horizontal scroll region at the existing narrow-width media
query (`overflow-x: auto; max-width: 100%` on the tablist,
`flex: 0 0 auto` on each tab) rather than letting the tab row force the
whole page wider -- re-measured afterward with zero page-level overflow
on every tab, including Rooms & Resources itself.

**Accessibility:** the Name and Capacity inputs are both `<label>`-wrapped
(no placeholder-only labeling); tab semantics (`role="tablist"`/`"tab"`/
`"tabpanel"`, roving `tabIndex`, Arrow/Home/End activation) are
unchanged and still correct with five tabs; Add/Edit/Delete disable via
a real `disabled` attribute (never color-only) with the existing
`aria-describedby` link to the lock banner text when locked; the inline
delete confirmation and all inline errors use the existing
`role="alert"`-based pattern already proven accessible by every other
School Setup tab.

**Tests added:** 8 frontend API tests
(`frontend/src/api/resources.test.ts`), 21 panel tests
(`frontend/src/components/setup/RoomsResourcesPanel.test.tsx` --
covering tab-load/render/no-raw-IDs/empty-state/create-defaults/
local-capacity-validation/create-success/backend-validation-message/
duplicate-name-message/edit-name/edit-capacity/edit-cancel/
delete-confirm-cancel/delete-success/RESOURCE_IN_USE via each reference
kind and both together/locked-state/lock-race/stale-refresh/
no-double-submit), and 4 new/updated `SchoolSetupPage.test.tsx` tests
(fifth-tab load, five-tabs-in-order, Arrow/Home/End wrap now covering
Rooms & Resources, `requestedTab: "rooms-resources"` navigation).

**Full regression:** core 479 passed/5 deselected (unchanged -- zero
backend production changes), `tests_web` 486 passed/zero skips
(unchanged -- zero backend production changes), frontend 460 passed/27
files/zero skips (428 + 32 new: 8 API + 21 panel + 3 net new
`SchoolSetupPage` cases, after accounting for pre-existing cases whose
assertions were extended in place), build clean, Alembic
`9fbec2126831`/one head/no drift, **zero new migration**. One
transient, order-dependent failure was observed in
`TimetablePage.test.tsx` during one full-suite run (an unrelated file
this slice never touched); confirmed to pass in isolation and to pass
cleanly on repeated full-suite runs immediately before and after --
treated as a pre-existing cross-file test-isolation flake, not a
regression introduced by this slice, and left unmodified per the "zero
backend/unrelated-frontend feature work" scope of this task. Scope audit
confirmed only the expected `frontend/src/api/resources.*`,
`frontend/src/components/setup/RoomsResourcesPanel.*`,
`frontend/src/pages/SchoolSetupPage.{tsx,test.tsx}`, and
`frontend/src/index.css` files changed -- zero backend files, zero
migration files, zero unrelated frontend files.

**Explicitly deferred/out of scope for C, and for the Resources MVP
phase as a whole:** Resource Availability; eligible-Resource sets;
Resource capabilities/categories; preferred Resource; solver-selected
Resources; seat/headcount capacity semantics. None of these are treated
as completeness blockers. Owner Decision #39 remains absent -- no
genuine unresolved product fork appeared during this task.

**RESOURCES C -- ROOMS & RESOURCES CATALOG FRONTEND CLOSED ON MAIN** --
implementation commit `58e8b5c`.

**RESOURCES MVP PHASE CLOSED.** With Slice C's catalog-management
surface now shipped, the Resources MVP phase is formally closed. MVP
shipped scope:
1. Resource catalog backend CRUD (Resources A)
2. Rooms & Resources catalog frontend CRUD (Resources C, this entry)
3. Ordinary Teaching Assignment fixed Resource (Resources B1)
4. Reserved Activity fixed Resource (Resources B2)
5. Aggregate cross-source Resource capacity (Resources B2)
6. Independent verification (Resources B2, zero production change
   needed -- already generic)
7. Configuration locking / generation race safety (reused across A/B1/
   B2/C, unchanged)
8. Timetable Resource display (Resources B1/B2)

**Explicitly deferred beyond the MVP phase (not blockers, not
scheduled):** Resource Availability; eligible Resource sets;
capabilities/categories; preferred Resource; solver-selected Resources;
seat/headcount capacity semantics. Owner Decision #39 remains absent
unless a genuine new product fork appears.

## CALENDAR A -- CALENDAR & BELL SCHEDULE BACKEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `a508023` ("feat: add
calendar and bell schedule backend") -- fast-forward merged from
`feature/calendar-backend` (base `695cf76`) onto `main`, with a separate
docs closure commit. Not pushed to any remote. **Calendar phase is NOT
closed** -- this slice ships the backend only; see `DECISIONS.md`'s
matching entry for the full design rationale.

**Scope shipped:**
1. `domain.calendar.Period` gains optional `start_time`/`end_time`
   (`datetime.time | None`) -- display/admin metadata only, never read
   by the solver, `validation.preflight`, or the verifier.
2. `domain.calendar` gains `clock_time_overlaps` (shared pure ordering
   check, reused identically by write-time validation and preflight),
   `derive_starts_new_block`/`recompute_block_ids` (the public
   `starts_new_block` abstraction over the internal `block_id`
   sequence -- raw `block_id` is never part of any public contract).
3. Arbitrary Day/Period counts, free-text names, Day/Period Up/Down
   move with contiguous `0..N-1` reindexing after every write, and a
   minimum-calendar invariant (>= 1 Day, >= 1 instructional Period)
   enforced in three independent layers (application `calendar_rules`,
   `preflight._check_calendar_shape`, and the DB's own
   `UNIQUE(academic_year_id, idx)` constraint).
4. New Periods are always `is_instructional=True`; the field is absent
   from the public write contract and never reassigned on update, so
   legacy `is_instructional=False` fixture rows survive untouched.
5. TimePreference index-drift safety: Period reorder is unconditionally
   blocked whenever any `TimePreference` exists in the Academic Year;
   Period delete is blocked precisely when it would retarget a stored
   `preferred_periods` index (exact match, or any later index that
   would shift down to fill the gap). Appending a new Period is always
   safe. No preference index is ever silently rewritten.
6. `GET .../calendar` (Day/Period projection, `configuration_locked`,
   never a raw `block_id`) and `POST/PUT/DELETE .../calendar/days[/
   {day_id}]` + `.../periods[/{period_id}]` plus `.../move` for both --
   `application.calendar_service`/`calendar_projection_service`,
   `persistence.calendar_repository` (two new SQLAlchemy adapters,
   Owner Decision #36's lock/recheck discipline reused verbatim, no
   new concurrency mechanism), `api.calendar_routes`.
7. `GET .../config`'s existing `PeriodResponse` gained the same two
   additive, nullable `start_time`/`end_time` `"HH:MM"` fields --
   confirmed backward compatible.
8. Migration `e0f73eda567b`: nullable `period.start_time`/`end_time`
   (`TIME`), revising `9fbec2126831`, one head, no drift. All 71
   pre-existing dev-database `period` rows confirmed to survive the
   upgrade with both columns `NULL`.

**Zero solver, verifier, or frontend production changes.** The School
Setup "Calendar & Bell Schedule" tab itself is explicitly out of scope
for this slice.

**New test coverage (120 new backend tests, zero frontend changes):**
+11 `tests/test_domain.py` (clock-time overlap detection incl.
touching-boundary-allowed/gap-allowed/reversed-overlap/missing-time-
skip/block-boundary-independence; `derive_starts_new_block`/
`recompute_block_ids` incl. marker-travels-with-identity-across-
reorder), +9 `tests/test_preflight.py` (no-Days/no-instructional-
Periods/minimum-valid-calendar/duplicate-or-non-contiguous-Day-and-
Period-index/clock-time-overlap), +47 new
`tests/test_calendar_service.py` (Day/Period create/update/delete/move
orchestration and validation against in-memory fake repositories,
mirroring `test_resource_service.py`'s discipline), +18
`tests_web/test_calendar_repository.py` (real-PostgreSQL round-trip,
reindex/block-recomputation, legacy `is_instructional=False`
preservation, TimePreference-blocked-reorder proven under the real
lock, Owner-Decision-#36 generation-vs-write race), +35
`tests_web/test_calendar_api.py` (full HTTP contract incl. exact
response shapes, HH:MM (de)serialization, `/config` backward-
compatibility, all locked/duplicate/in-use/reorder-blocked error
codes).

**Final verified baselines:** core 546 passed/5 deselected (+120 new,
described above), `tests_web` 539 passed/zero skips (+53 new: 18 + 35
above), frontend 460 passed/27 files/zero skips (unchanged -- zero
frontend changes in this slice), build clean, Alembic
`e0f73eda567b`/one head/no drift.

**CALENDAR A -- CALENDAR & BELL SCHEDULE BACKEND CLOSED ON MAIN** --
implementation commit `a508023`. **Calendar phase remains NOT closed.**
**Next slice: Calendar B -- School Setup "Calendar & Bell Schedule"
frontend** (the tab itself: Day/Period list, create/rename/delete,
Up/Down reorder, optional HH:MM bell times, lunch/break shown as a
visual gap, all backed by the Calendar A API above). No manual
timetable editing is in scope for Calendar B either.

## CALENDAR B -- SCHOOL SETUP FRONTEND CLOSED ON MAIN

Sixth `SchoolSetupPage` tab, "Calendar & Bell Schedule", over the
unmodified Calendar A backend:

1. `frontend/src/api/calendar.ts` -- a new, dedicated client module
   (`getCalendar`/Day and Period create/update/delete/move) matching
   `resources.ts`'s one-domain-per-module convention; no second
   frontend-facing Calendar API shape.
2. `CalendarBellSchedulePanel.tsx` -- two visually distinct sections,
   "Working Days" and "Bell Schedule", each its own bordered card with
   a `setup-table`; never renders a raw `id`/`index`/`block_id`.
3. Day CRUD + accessible Up/Down move (first row's Up / last row's Down
   locally disabled); Period CRUD + move, with optional paired HH:MM
   times (`type="time"` inputs, both-empty-or-both-present validated
   locally before submit) and the `starts_new_block` toggle (hidden and
   forced `true` for the first Period, editable for every later one,
   with an explanatory callout -- never a raw `block_id` control).
4. Legacy `is_instructional=False` Periods remain visible with a
   "Non-instructional" badge and explanatory hint; `PeriodWriteRequest`
   has no `is_instructional` field at all, so no form can flip one.
5. Every mutation (create/update/delete/move, for both Days and
   Periods) is followed by a fresh authoritative `GET .../calendar` --
   the `move` endpoint's own already-recomputed projection response is
   deliberately not trusted for the re-render, keeping one single
   refresh code path for all eight mutation kinds.
6. Friendly, code-driven error mapping for every locked Calendar A
   error (`DUPLICATE_DAY`/`DUPLICATE_PERIOD`, `DAY_IN_USE`/
   `PERIOD_IN_USE` incl. the `TIME_PREFERENCE` delete-safety case,
   `INVALID_DAY`/`INVALID_PERIOD` per validation code,
   `PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES` with the exact locked
   wording and no force-reorder option, `SCHEDULING_CONFIGURATION_LOCKED`
   reusing `RoomsResourcesPanel`'s lock-race pattern) -- no raw backend
   vocabulary is ever shown.
7. Configuration lock and stale-authority discipline reused verbatim
   from `RoomsResourcesPanel`. Six-tab keyboard navigation (Arrow keys/
   Home/End wrapping) and the existing narrow-width tablist horizontal
   scroll (Resources C) both cover the new tab unchanged.

**Zero backend, migration, solver, or verifier production changes.**

**New test coverage (91 new frontend tests):** 25
`frontend/src/api/calendar.test.ts`, 39
`CalendarBellSchedulePanel.test.tsx`, 2 new + several updated
`SchoolSetupPage.test.tsx` cases for the six-tab contract.

**Final verified baselines:** core 546 passed/5 deselected (unchanged),
`tests_web` 539 passed/zero skips (unchanged), frontend 526 passed/29
files/zero skips (+66 new), build clean, Alembic
`e0f73eda567b`/one head/no drift/zero new migration.

**Real-browser acceptance: BLOCKED, not performed** -- the Claude in
Chrome extension could not be connected in this session; the Day/Period
CRUD-and-reorder flows, lunch-gap representation, and 375px narrow-
viewport checks described in the Calendar B task were never exercised
in a real browser. This should be completed in a follow-up session
before the UI is considered fully accepted; see `DECISIONS.md`'s
Calendar B entry for the full note.

**CALENDAR B -- SCHOOL SETUP FRONTEND CLOSED ON MAIN** -- implementation
commit `58dd405`.

## CALENDAR MVP PHASE CLOSED

Shipped: arbitrary Working Days and instructional Periods, Day/Period
CRUD, accessible Up/Down ordering, optional paired bell times,
lunch/break as clock gap + block boundary, the `starts_new_block`
abstraction, TimePreference index-drift protection, minimum-calendar
invariants, configuration lock/generation-race protection, and the
School Setup "Calendar & Bell Schedule" frontend. The solver remains
calendar-shape agnostic throughout (zero solver/verifier production
changes across both Calendar A and B).

**One item of the originally-scoped closure checklist is not actually
satisfied: real-browser CRUD/reorder acceptance was BLOCKED** (browser
tooling unavailable this session) rather than performed -- every other
item is implemented, tested, and merged to `main`. Explicitly deferred
beyond this MVP: explicit visible Lunch/Break rows, editable
non-instructional Period creation, calendar dates/holidays/exceptions,
rotating A/B weeks, multiple bell schedules by weekday, manual
timetable editing, migrating `TimePreference` away from raw period
indexes. Resource Availability remains separately deferred.

**Owner Decision #39 remains absent.**

**Next major product area (recommended): admin-facing manual timetable
editing, locking, and re-optimization.** School Setup catalogs, Teacher
Availability, Teaching Assignments, Reserved Activities, Rooms &
Resources, the Calendar/Bell Schedule, schedule generation, and the
class/teacher timetable display all already exist -- manual editing
over a generated schedule is the natural next slice. Not implemented in
this slice.

## CALENDAR MVP REAL-BROWSER ACCEPTANCE COMPLETED

Forward-only follow-up: the "Real-browser acceptance: BLOCKED" note
above was accurate for that session (Claude in Chrome never connected).
It remained unavailable in this follow-up too, so the human user
completed the walkthrough manually against the same identified review
dataset (`reserved-activity-review-school` /
`ay-reserved-activity-review-2026`), operating a normal browser against
a locally restarted Vite dev server proxying the existing backend,
while this session prepared the dataset/environment and independently
verified the results (not just the user's report) directly against the
database and API before and after.

User-verified, all PASS: Working Days create/rename/Move Up/Move
Down/delete with the original 3-day set restored; Bell Schedule
create/rename+retime/`starts_new_block` toggle/Move Up/Move Down/delete
with the original 5-period set restored; local HH:MM validation
(only-Start, only-End, equal, reversed all rejected; valid Start<End
accepted); the legacy Lunch row visible and labeled Non-instructional
with no instructional-state toggle and no "Add lunch"/"Add break"
control; and the ~375px narrow layout with no page-level horizontal
overflow and every control still usable.

**Dataset restoration independently confirmed** via direct DB query and
a live `GET .../calendar` call: exactly Monday/Tuesday/Wednesday at
indexes 0-2 and exactly Period 1-4 + Lunch at indexes 0-4, byte-for-byte
matching the pre-session baseline; zero `Schedule`/`ScheduleVersion`/
`TimePreference` rows; `configuration_locked` still `false`.
`frontend/.env.local` restored to `synthetic-school`/`ay-2026` and Vite
restarted against it.

**No Calendar production-code change was required.** Focused frontend
suites (`calendar.test.ts`, `CalendarBellSchedulePanel.test.tsx`,
`SchoolSetupPage.test.tsx`): 87 passed. Build clean. Alembic
`e0f73eda567b`, one head, no drift -- unchanged. Full backend/
`tests_web` regression was not rerun (docs-only follow-up, no backend
code changed; Calendar B's own baselines remain authoritative).

**The previously outstanding acceptance gap is now satisfied. Calendar
B is fully accepted. CALENDAR MVP IS FULLY ACCEPTED.** Deferred scope
is unchanged. **Owner Decision #39 remains absent.**

## FULL TIMETABLE END-TO-END ACCEPTANCE COMPLETED

Dataset: `synthetic-school` / `ay-2026`, which already carried a
pre-existing active schedule -- Schedule id 2, active `ScheduleVersion`
1 (`OPTIMAL`, `total_soft_penalty 0`, 160 `ScheduleEntry` rows, created
2026-09-07). This closure proves the *existing* generated timetable
end-to-end, not a fresh generation run.

**Automated proof:** the existing, unmodified independent
`verification.verifier.verify()` ran against the real persisted
`SchedulingProblem` + active schedule -- **passed, zero violations**
across all 12 checks. Independently cross-checked: exact-full 40/40
occupancy for all four class sections (160 total, correctly accounting
for the German/Russian split group as one occupancy unit); zero teacher
double-bookings across all 8 teachers; both Teacher Science
`UNAVAILABLE` slots and the one Teacher History `PREFER_NOT` slot
correctly unscheduled; both `ReservedBlock`s (Chess Club, Robotics
Club) placed exactly as configured with their joint classes; Indoor Gym
capacity never exceeded; the split-group and merged-class (History
9a/9b) requirements scheduled correctly; both REQUIRED and PREFERRED
block patterns formed exactly as configured. The class and teacher
timetable API projections were called directly and found structurally
consistent with the DB-derived counts.

**Human real-browser proof:** the user visually confirmed all four
class timetables (8a/8b/9a/9b) fully filled with no collisions, both
Reserved Activities in their expected joint slots, and every teacher
timetable (not just the automated sample) correct. No defect found.

**Important distinction:** this is acceptance of the existing active
schedule, not a demonstration of a fresh `Generate` run --
`synthetic-school` already has an active `Schedule`, and Decision #31's
locked initial-generation-only contract correctly refuses a second one
(`409 SCHEDULE_ALREADY_EXISTS`). A fresh-generation demonstration
remains open for later, on a separate dedicated dataset, never by
resetting this known-good baseline.

**FULL TIMETABLE END-TO-END ACCEPTANCE CLOSED ON MAIN** -- docs-only,
zero production-code change.

**Next major product area (recommended, now fully justified): admin-
facing manual timetable editing, locking, and re-optimization.** Every
prerequisite -- School Setup catalogs, Teacher Availability, Teaching
Assignments, Reserved Activities, Rooms & Resources, Calendar MVP, and
now the full generated timetable itself -- is accepted. Not implemented
in this task.

## FRESH TIMETABLE GENERATION END-TO-END ACCEPTANCE COMPLETED

Dataset: `generation-review-school` / `ay-generation-review-2026`,
seeded clean (0 `Schedule`/`ScheduleVersion`/`ScheduleEntry`/
`LockedOccurrence`) via the existing test-only
`tests_web/support/problem_writer` writer against a renamed copy of
`build_valid_fixture()` -- no production code added to create it.

**Generation result:** real `POST .../schedule/generate` -> `HTTP 201`,
`version_number 1`, `OPTIMAL`, `total_soft_penalty 0`, active version 1,
`parent_version_id` null, 160 `ScheduleEntry` rows, 0
`LockedOccurrence` rows.

**Independent proof:** verifier `passed: True`, zero violations;
exact-full occupancy on all four classes; zero teacher collisions;
availability (`UNAVAILABLE`/`PREFER_NOT`) respected; both reserved
activities correct; resource capacity respected; split group
synchronized; merged class coherent; REQUIRED block pattern valid.

**Projection proof:** every class and every teacher timetable view
verified correct via the real API.

**Idempotency:** a second `POST .../schedule/generate` -> `HTTP 409
SCHEDULE_ALREADY_EXISTS`, zero mutation.

**Human browser proof:** the user reviewed every class/teacher
timetable view in the browser and confirmed everything displayed
correctly. No defect reported.

**THE INITIAL TIMETABLE GENERATION PRODUCT PATH IS NOW END-TO-END
ACCEPTED FROM A CLEAN DATASET THROUGH BROWSER DISPLAY.**

**Two datasets, kept deliberately distinct:** `synthetic-school`/
`ay-2026` remains the previously-generated regression baseline
(untouched by this slice); `generation-review-school`/
`ay-generation-review-2026` is now the independently-proven
fresh-generation baseline -- it already carries its accepted
`ScheduleVersion` 1 and is no longer "clean"; neither dataset is to be
regenerated or reset to re-run this proof again.

**FRESH TIMETABLE GENERATION END-TO-END ACCEPTANCE CLOSED ON MAIN** --
docs-only, zero production-code change, no migration.

**Next major product area: admin-facing manual timetable editing
frontend.** Every backend capability -- move/swap, lock, unlock,
re-optimize, immutable `ScheduleVersion` creation, stale-version
protection, truthful soft-penalty metadata, the independent-verifier
gate, REQUIRED-block-safe move validation -- already exists and is
accepted. Only the UI slice remains. Not implemented in this task.

## MANUAL TIMETABLE EDITING MVP END-TO-END ACCEPTANCE COMPLETED

Closes the UI slice the previous entry left open. Accepted behavior,
proven both technically and by the user's own real-browser testing:

- class-view, click-based editing (no drag-and-drop)
- Move/swap with an explicit confirmation step before any request is sent
- server-authoritative constraint validation (`validate_move`) -- the
  frontend never re-implements a scheduling rule of its own
- a valid Move creates a new immutable `ScheduleVersion`; an invalid one
  creates none, with the specific backend rejection reason displayed
  (never a generic failure message)
- Lock / Unlock, each creating a new `ScheduleVersion` while carrying the
  base version's own truthful `solver_status`/`total_soft_penalty`
  forward unchanged (entries are unchanged by a lock/unlock)
- Re-optimize, with an explicit confirmation step, honoring every locked
  occurrence and persisting the real solver's own metadata
- stale-`base_version_number` protection (`409 STALE_SCHEDULE_VERSION`)
  on every mutating command, surfaced as a refresh notice rather than a
  raw error
- the independent verifier and REQUIRED-block-safe move validation
  (`docs/DECISIONS.md`'s manual-editing correction slice) sit behind
  every mutation, unchanged by this UI work
- split-group logical-occurrence handling (moving/locking one half moves
  or locks its sibling too, exactly as the domain layer already required)
- Reserved Activities remain fixed, non-editable placements -- never
  selectable as a Move/Lock source
- the grid's active version refreshes automatically after every
  successful mutation, with no full-page reload
- **move-target preview:** entering Move mode calls a dedicated,
  read-only `POST .../schedule/active/move/preview` endpoint (reusing
  `validate_move` directly, never a simplified frontend validator) and
  colors every candidate cell before the admin ever attempts a move --
  BLUE for the selected source, GREEN + ✓ for a backend-confirmed
  allowed destination, RED + × for a forbidden one (with its violation
  reason inspectable on click/focus/hover, without sending any move
  request), and neutral/gray while the preview is loading or if it
  fails -- an unevaluated cell is never treated as allowed.

**Human real-browser acceptance (explicit, performed by the user):** on
`editing-review-school`/`ay-editing-review-2026` -- a valid Move (Class
8-A, Science) succeeded and the active version increased; an invalid
Move (the fixed Art lesson) was correctly rejected with "This move isn't
allowed." plus the fixed-placement reason, and caused no mutation; Lock
showed a visible locked state and increased the version, with Move
disabled while locked; Unlock removed the locked state, re-enabled Move,
and increased the version; Re-optimize completed successfully with a
new active version and no error; and for move-target preview, the
source cell displayed distinctly, valid targets displayed GREEN + ✓,
forbidden targets displayed RED + ×, a forbidden target's reason was
visible, an allowed target could proceed to confirmation, and the user
confirmed the overall UX works and is good.

**Technical proof (already completed in the implementation task):** the
preview endpoint reuses the authoritative `validate_move` and creates
zero persistence writes (proven both by a dedicated unit test and by a
direct before/after row-count check against the real dev database);
549/549 frontend tests, 575/575 core backend tests, 568/568 `tests_web`
integration tests, a clean production build, Alembic head unchanged at
`e0f73eda567b`, no migration.

**Dataset roles, as they stand now:** `generation-review-school`/
`ay-generation-review-2026` remains the protected, untouched
fresh-generation baseline (still exactly `ScheduleVersion` 1, `OPTIMAL`,
penalty 0) and must not be mutated or reset. `editing-review-school`/
`ay-editing-review-2026` is the disposable manual-editing acceptance
dataset and now intentionally carries multiple `ScheduleVersion` rows
from real human editing acceptance -- this is expected, not a defect.
`synthetic-school`/`ay-2026` was accidentally mutated by real Move/Lock/
Reoptimize UI actions during earlier human editing review, before the
disposable dataset existed, and progressed from its original version 1
to version 7 as a result -- **it is no longer considered the pristine
version-1 regression baseline it was before**; no destructive repair was
attempted in this closure, and none of this task's own work (automated
tests included) mutated it any further.

**THE ADMIN MANUAL TIMETABLE EDITING MVP IS NOW END-TO-END ACCEPTED.**

**MANUAL TIMETABLE EDITING MVP ACCEPTANCE CLOSED ON MAIN** -- docs-only,
zero production-code change, no migration.

**Next major product area: schedule version history + restore.** Show
the immutable `ScheduleVersion` history for a school/year (version
number, timestamp, solver status, penalty), distinguish the current
active version, let the admin inspect an older version, and let them
safely restore one. Restoring must never mutate an old immutable
`ScheduleVersion` in place -- the intended semantics for that design
slice are: select a historical version, create a NEW immutable
`ScheduleVersion` copied from it, parent it from the currently-active
version, and promote the new version active, preserving append-only
history and giving a safe Undo/Restore behavior. Not implemented in
this task.

## SCHEDULE VERSION HISTORY + RESTORE MVP END-TO-END ACCEPTANCE COMPLETED

Closes the design slice the previous entry left open. Accepted
behavior, proven both technically and by the user's own real-browser
testing:

- immutable `ScheduleVersion` history, listed newest-first, with a
  clear active-version indicator
- historical class timetable inspection at any specific past version
- historical teacher timetable inspection at any specific past version
- historical mode is strictly read-only -- no Move, Lock, Unlock, or
  Re-optimize control is ever offered while viewing a past version
- a safe restore confirmation that explains a NEW version will be
  created (never phrased as "rolling back" or implying data loss)
- Restore creates a NEW immutable `ScheduleVersion` -- the historical
  source version is never reactivated in place and never mutated
- the restored version's entries come exactly from the historical
  source, never re-derived or re-solved
- the restored version's locked occurrences come exactly from the
  historical source, never inherited from whatever the version that was
  active immediately before restoring happened to have locked
- the new version's parent is the version that was active immediately
  before the restore, preserving a genuine, unbroken lineage
- stale-`base_version_number` protection, the same `409
  STALE_SCHEDULE_VERSION` contract every other mutating editing command
  already uses
- the independent verifier re-checks the historical source's entries
  against the current configuration before persisting (defense-in-depth)
- every prior `ScheduleVersion` is preserved untouched -- restoring is
  strictly additive, never destructive

**Human real-browser acceptance (explicit, performed by the user):**
Version History opened correctly; Version 9 was shown as ACTIVE;
Version 1 opened in historical read-only mode, clearly labeled "Viewing
historical Version 1 — read only"; Class and Teacher historical viewing
both worked; every editing action was correctly unavailable in
historical mode; "Restore this version" was offered and its
confirmation shown; restoring Version 1 (while Version 9 was active)
created a NEW Version 10, which became the active version; Version 1
remained untouched historical data; success feedback was shown; the
full history (Versions 1-9) remained preserved rather than rolled back
or deleted.

**Independent post-acceptance verification (read-only, no further
mutation performed):** `editing-review-school`/`ay-editing-review-2026`
now has 10 `ScheduleVersion` rows with exactly one active (Version 10);
Version 10's `parent_version_number` is 9; Version 10's `entries`
(160/160) and `locked_occurrences` (both empty) exactly equal Version
1's; Versions 1-9 remain present and unchanged; the independent
verifier passed against Version 10's entries with zero violations.

**Concrete acceptance, stated plainly:** Version 1 restored while
Version 9 was active -> NEW Version 10 created -> Version 10 became
active -> Version 1 remained historical and immutable -> Versions 2-9
remained preserved.

**THE SCHEDULE VERSION HISTORY + RESTORE MVP IS NOW END-TO-END
ACCEPTED.**

**Dataset status:** `generation-review-school`/`ay-generation-review-2026`
remains untouched (still exactly `ScheduleVersion` 1, `OPTIMAL`, penalty
0). `synthetic-school`/`ay-2026` remains at its already-reclassified
state (version 7, no longer the pristine baseline), unchanged by this
closure. `editing-review-school`/`ay-editing-review-2026` now reflects
the accepted restore (10 versions, Version 10 active) -- this is the
dataset's expected, intentional disposable-acceptance state, not a
defect.

**SCHEDULE VERSION HISTORY + RESTORE MVP ACCEPTANCE CLOSED ON MAIN** --
docs-only, zero production-code change, no migration.

**Next major product area: safe configuration changes after schedule
generation.** The system currently locks scheduling configuration
outright once a `Schedule` exists (Add Teacher, Add Class, Add Subject,
Add Assignment, availability/resource changes, etc. all become
disabled) -- safe for data integrity, but real schools need to change
configuration after a timetable has already been generated. The next
product/design slice should define a safe workflow: configuration
locked -> admin explicitly chooses "Edit scheduling configuration" ->
the current timetable/history remain preserved -> configuration changes
are made under a controlled mode -> the existing active schedule
becomes clearly stale/out-of-date -> the admin must regenerate/
re-optimize against the new configuration -> no silent mutation of any
historical `ScheduleVersion`. Not implemented in this task.

## SAFE CONFIGURATION CHANGES -- SLICE A -- CONFIGURATION REVISION
FOUNDATION CLOSED ON MAIN

Locked owner decisions for the overall safe-configuration-changes
product area (Stale timetable UX = A: banner + disabled editing, no
blocking interstitial; Discard Draft = B: requires explicit
confirmation; draft-editing MVP scope = A: must eventually cover every
`SchedulingProblem` entity, not just Teacher+Assignment; incompatible
locks during future regeneration = B: identify affected locks, show
admin, require explicit confirmation) are recorded but **not yet
implemented** -- Slice A is schema/persistence foundation only.

A first-class `ConfigurationRevision` model now exists: surrogate PK,
`academic_year_id`, `revision_number` (meaningful only within one
`AcademicYear`, enforced via `UNIQUE(academic_year_id, revision_number)`),
lifecycle `status` (`DRAFT`/`PUBLISHED`), `created_at`. `AcademicYear`
gained nullable `published_revision_id`/`draft_revision_id` pointers
(circular FKs via `use_alter=True`, mirroring the existing
`Schedule.active_version_id` pattern). `PUBLISHED` means "this revision
was finalized and is permanently immutable", NOT "this is the one
currently-active published revision" -- a year may accumulate any
number of historical PUBLISHED revisions over its lifetime;
`published_revision_id` alone identifies which one is currently
authoritative. At most one DRAFT revision per year is enforced at the
database level via a partial unique index; there is deliberately no
equivalent constraint on PUBLISHED. (**Correction, same slice:** an
earlier version of this migration mistakenly also capped PUBLISHED at
one per year -- fixed by a same-day follow-up migration before this
was ever relied upon; see the dedicated decision entry below.)

**Immutable revision ownership.** Every one of the fifteen
`SchedulingProblem` configuration tables (`Day`, `Period`,
`ClassSection`, `ParticipantGroup`, `ParticipantGroupClassSection`,
`Teacher`, `TeacherAvailability`, `Activity`, `Resource`,
`TeachingRequirement`, `TimePreference`, `ReservedBlock`,
`ReservedBlockClassSection`, `ReservedBlockSlot`, `FixedPlacement`) now
carries a non-nullable `configuration_revision_id`. Natural IDs are
unique *within* a revision, never globally -- two revisions of the same
year may permanently coexist with rows sharing the same natural ID
(proven by a dedicated persistence test). Every composite FK between
two config tables was widened to include `configuration_revision_id` on
both sides, so a row in one revision can never structurally reference a
row from a different revision (proven by a dedicated persistence test
that a cross-revision reference is rejected by the database itself, not
just application code). `ScheduleVersion` gained a non-nullable
`configuration_revision_id` (RESTRICT on delete): every version --
initial generation, manual edit, lock/unlock, re-optimize, restore --
permanently identifies the exact revision it was built against.

**Initial-setup / first-generation semantics.** A new `AcademicYear`
now always receives an initial editable DRAFT revision
(`revision_number=1`) transactionally at creation; `published_revision`
starts null. Configuration writes before the first successful Generate
target this draft. The FIRST successful Generate, at final persist
(inside the same short transaction that already takes the
Owner-Decision-#36 `AcademicYear` row lock and re-verifies the solved
configuration didn't change), atomically: publishes that exact draft
(`status` -> `PUBLISHED`), sets it as the year's `published_revision`,
clears `draft_revision` to null, and creates the first `ScheduleVersion`
referencing that now-published revision. No `ScheduleVersion` is ever
created referencing a still-mutable revision. If the solve or the
comparison fails, the draft remains DRAFT and fully editable, and
nothing is persisted. A second `persist_initial_version` call for a
year that already has a `Schedule` (draft already published, so
`draft_revision_id` is null) still correctly falls through to the
pre-existing `ScheduleAlreadyExistsError` conflict path rather than
being misdiagnosed as a missing-draft error.

**Historical/active projection correctness.** `SchedulingProblemRepository`
gained `load_for_revision(school, year, revision_number)`, loading one
specific revision by its natural revision number (never a surrogate DB
ID across any application/domain boundary). Class/Teacher timetable
projection and manual-editing/reoptimize load now resolve the version's
*own* `configuration_revision_number` first, then load the
`SchedulingProblem` for that exact revision -- historical Version N's
class/teacher names and calendar always come from Version N's own
revision, never from "whatever is currently published." Existing
browser-visible behavior is unchanged today because every existing
`ScheduleVersion` migrated to the same single revision.

**Configuration write behavior is UNCHANGED in this slice.** Before a
Schedule exists, configuration writes still work exactly as before,
now internally targeting the draft revision. After a Schedule exists,
the existing `SCHEDULING_CONFIGURATION_LOCKED` behavior remains in full
force -- Add Teacher/Add Class/Add Subject/etc. all remain disabled.
**No safe post-generation configuration editing is implemented yet.**
There is no "Edit scheduling configuration" action, no draft-fork-from-
published, no stale-timetable banner, no Discard Draft, no Regenerate-
after-config-change -- all of that is Slice B and later. This slice
only proves the schema/persistence foundation those features will need
(same natural IDs safely coexisting under a new revision was proven by
a dedicated persistence test), and implements zero frontend change.

**Migration.** A single new Alembic migration (`83434054f9d2`, revises
`e0f73eda567b`) backfills every pre-existing `AcademicYear`: creates
exactly one `ConfigurationRevision` (`revision_number=1`), marks it
PUBLISHED if that year already has a `Schedule` else DRAFT, points the
year's `published_revision`/`draft_revision` accordingly, and backfills
every existing config row and every existing `ScheduleVersion` to that
revision -- an exact historical backfill, no schedule data rewritten or
regenerated, nothing deleted.

**Real dev database migration applied and independently verified
(read-only, no mutation):** all 14 `AcademicYear` rows (the 3 tracked
datasets plus 11 other local review/CRUD datasets discovered during
this slice) each have exactly one `ConfigurationRevision`, correctly
PUBLISHED (with a Schedule) or DRAFT (without one), zero NULLs remain
in any `configuration_revision_id` column across any of the fifteen
config tables or `schedule_version`.
`generation-review-school`/`ay-generation-review-2026`: 1
`ScheduleVersion`, active Version 1, OPTIMAL, penalty 0, revision 1.
`editing-review-school`/`ay-editing-review-2026`: 10 `ScheduleVersion`
rows, active Version 10, all ten reference revision 1; the independent
verifier re-run against active Version 10 (loaded via
`load_for_revision`) passed with zero violations.
`synthetic-school`/`ay-2026`: 7 `ScheduleVersion` rows, active pointer
and history unchanged (Version 7 active), all seven reference revision
1.

**Regression:** focused migration-safety tests (`tests_web/
test_persistence_schema.py` 12/12, `tests_web/test_schedule_schema.py`
15/15) green; full `tests -m "not slow"` 593/593 green; full
`tests_web` 592/592 green; frontend `npm test -- --run` 572/572 green
(zero frontend files changed) and `npm run build` clean; `alembic
check` reports zero drift against the migrated dev database.

**SAFE CONFIGURATION CHANGES -- SLICE A ACCEPTANCE CLOSED ON MAIN --
schema/persistence foundation only. Slice B (draft fork/discard
lifecycle, stale-timetable UI, "Edit scheduling configuration") is not
started.**

**Correction (same day):** the originally-applied migration mistakenly
also enforced at most one PUBLISHED `ConfigurationRevision` per
`AcademicYear` -- wrong, since PUBLISHED means "permanently immutable
once finalized," not "the current one," and a year must be able to
accumulate multiple historical PUBLISHED revisions over its lifetime.
Fixed by follow-up migration `398b05641152`, which drops only the
erroneous index; the at-most-one-DRAFT index is untouched and still
correct. Two new persistence tests prove multiple PUBLISHED revisions
may coexist and that the future regeneration publish transition is
schema-valid. Applied to the local dev database and independently
re-verified: all three tracked datasets and the eleven other local
datasets unchanged. Full detail in `docs/DECISIONS.md`.

## SAFE CONFIGURATION CHANGES -- SLICE B -- CONFIGURATION DRAFT
LIFECYCLE IMPLEMENTED, REVIEWED, VERIFIED -- READY TO COMMIT

**Scope:** the draft configuration lifecycle itself -- opening an
editable draft after Generate, eagerly cloning the published
configuration into it, redirecting configuration reads/writes to it,
discarding it, and making the active timetable read-only while it is
open. Builds entirely on Slice A's `ConfigurationRevision` schema; no
migration was needed.

**New port, single lock/state authority:** `ConfigurationRevisionRepository`
(`get_state`/`begin_draft`/`discard_draft`), implemented by
`SqlAlchemyConfigurationRevisionRepository`, replaces the old "does a
Schedule exist" signal every configuration writer and every Setup
projection used. `configuration_write_lock.reject_if_configuration_locked`
now means exactly: **locked iff no draft is open** -- not "iff a
Schedule exists." Before Slice B those two conditions were identical
(a draft was cleared the moment Generate published it, and never set
again); Slice B's `begin_draft` reopens a draft for a year that already
has a Schedule, and writes must succeed again once it does, redirected
to that draft, never to the immutable published revision. All 9
configuration write services (Teacher, Class Section, Subject, Special
Activity, Resource, Calendar, Teaching Assignment, Teacher
Availability, Reserved Activity) and all 9 Setup projection services
were updated to this single authority -- each write service's own
redundant fast "is it locked" precheck was deleted outright (the
repository is the sole source of truth; a service-level precheck of
"is a draft open" would have been pure duplication).

**Eager clone.** `begin_draft`, on a published/no-draft year, creates a
new `ConfigurationRevision` (next `revision_number`, `status=DRAFT`)
and copies every one of the fifteen `SchedulingProblem` configuration
tables (Day, Period, ClassSection, ParticipantGroup,
ParticipantGroupClassSection, Teacher, TeacherAvailability, Activity,
Resource, TeachingRequirement, TimePreference, ReservedBlock,
ReservedBlockClassSection, ReservedBlockSlot, FixedPlacement) from the
published revision into it, in dependency order. Every one of the 21
intra-configuration FK edges is remapped to the NEW draft's own rows
via old-surrogate-id -> new-surrogate-id maps built as each table is
cloned -- never a raw copy of a published-revision surrogate FK value.
Natural IDs and every scalar field are preserved byte-for-byte. The
whole clone runs under the same `AcademicYear` `SELECT ... FOR UPDATE`
row lock (Owner Decision #36) every configuration writer already uses,
held from before the first insert through the final `commit()` --
proven under genuine concurrent load (two real, independently
committed PostgreSQL sessions racing via `threading.Barrier`, with
direct timing evidence showing ~87ms of overlap out of a ~90ms total
duration) to serialize two simultaneous `begin_draft` calls into
exactly one draft, zero duplicate revisions, zero duplicate clone rows,
no deadlock. `begin_draft` is idempotent: called again while a draft is
already open (or before the year's first Generate, when the initial
draft already exists), it returns the existing draft unchanged --
no re-clone, no second revision.

**Draft-first reads and writes.** `SchedulingProblemRepository.
load_by_school_and_year` now resolves the year's open DRAFT first, its
PUBLISHED revision otherwise (previously published-first, a
distinction invisible until a second revision could ever coexist).
Every Setup screen and every configuration writer's own `validate`
closure therefore see the configuration actually being edited, never
the frozen published one, the moment a draft is open. The published
revision remains completely untouched throughout -- proven both by
direct row-snapshot equality in the repository tests and by a real
HTTP acceptance test (create a Teacher while a draft is open; the new
row belongs to the draft revision; the published revision's own
Teacher rows are verified byte-for-byte unchanged; `GET .../teachers`
reflects the new teacher while the draft is open and reverts to
exactly the pre-draft list once it is discarded).

**Discard lifecycle.** `discard_draft` clears the draft pointer and
hard-deletes the draft revision (cascading to all fifteen tables'
draft-scoped rows), restoring the published-only state -- the
published revision and every `Schedule`/`ScheduleVersion`/
`ScheduleEntry`/`LockedOccurrence` row are untouched. Guarded: refuses
with `NoConfigurationDraftError` if no draft is open, with
`InitialDraftCannotBeDiscardedError` if the year's only revision is its
initial pre-first-Generate draft (required for that first Generate to
ever succeed), and -- defense-in-depth, a bare `RuntimeError`, since
Slice A's own invariant makes this state unreachable through any
legitimate call path -- if a draft is somehow still referenced by a
`ScheduleVersion`. Every rejected discard performs zero mutation.

**Stale-timetable mutation guard (Owner Decision 1).** `Configuration
RevisionState.timetable_out_of_date` is `True` exactly when a Schedule
exists AND a draft is open. `ScheduleEditingService` gained a
`ConfigurationRevisionRepository` dependency and a shared
`_reject_if_out_of_date` guard, called by `move`/`lock`/`unlock`/
`reoptimize`/`restore` -- every one of the five rejects with the new
`ScheduleOutOfDateError` while a draft is open, with zero mutation
(verified by real-DB tests asserting `ScheduleVersion`/
`LockedOccurrence` counts are unchanged after each rejected attempt).
`preview_move` deliberately never calls this guard and remains
callable throughout -- it is read-only and persists nothing regardless
of staleness. Mutations succeed again immediately once the draft is
discarded (the guard reads live state on every call, not a cached
flag). **A real production gap was found and fixed while adding this
guard's own integration coverage:** `ScheduleOutOfDateError` had never
actually been wired into `api/schedule_routes.py`'s exception handling
for the five mutating routes, so it would have surfaced as an
uncaught 500 rather than the intended `409 SCHEDULE_OUT_OF_DATE` --
caught by the new tests before this slice was ever considered done,
fixed the same session, and reconfirmed with 7 new real-HTTP
integration tests plus the existing 33 unaffected.

**New HTTP surface:** `GET/POST/DELETE /schools/{school_id}/years/
{year_id}/configuration/{state,draft}` -- read revision state, open a
draft, discard a draft. Response body: `{published_revision_number,
draft_revision_number, configuration_locked, timetable_out_of_date}`,
never a persistence surrogate ID. New stable error codes: `409
NO_CONFIGURATION_DRAFT`, `409 INITIAL_DRAFT_CANNOT_BE_DISCARDED`, `409
SCHEDULE_OUT_OF_DATE` (the last one shared with the five schedule-
editing routes above).

**Historical published revisions remain valid.** Slice B's own code
never creates or mutates a `PUBLISHED` row -- `begin_draft`/
`discard_draft` only ever touch the DRAFT. The multi-published-revision
invariant this depends on was fixed and tested in the Slice A
correction above; nothing in Slice B's diff can regress it.

**No migration, zero frontend change.** Slice B is built entirely on
Slice A's existing schema (`alembic check` reports zero drift
throughout); no new migration was needed or created. Zero frontend
files changed -- no draft banner, no Discard button, no Regenerate
button, no Out-of-date banner exist yet; these routes exist only so a
later UI slice has something to call.

**Regression (chronology, stated accurately):** the full baseline below
was captured BEFORE this closure's two small audit-cleanup edits (a
docstring clarification and one strengthened test assertion, both
non-functional); only the one directly affected file was re-run after
those two edits, and stayed green (13/13).
- Core `tests -m "not slow"`: **600 passed, 5 deselected**.
- `tests_web` (full): **627 passed**.
- Frontend: `npm test` **572/572 passed**; `npm run build` clean.
- Alembic: `current` = `heads` = `398b05641152` (one head); `alembic
  check` reports no new upgrade operations.
- Real dev-database datasets: `generation-review-school` (1 version,
  active v1, OPTIMAL, penalty 0), `editing-review-school` (10 versions,
  active v10), `synthetic-school` (7 versions, active v7) -- all still
  exactly matching the Slice A/correction baseline, all still revision
  1; the other 11 local datasets and the migration state (`398b05641152`)
  also unchanged. Zero data mutation performed during verification.
- Test database (`school_timetable_test`): confirmed empty (0 schools,
  0 years) after the full integration/concurrency run -- every test's
  cleanup, including the concurrency test's manual cascade delete,
  left zero residue.
- Concurrency: two independent, genuinely overlapping PostgreSQL
  transactions (real threads, real row lock, not sleep-based timing)
  converged on exactly one draft revision, with every one of the
  fifteen tables cloned exactly once -- no duplicate revision, no
  duplicate clone rows, no deadlock.
- Final diff audit (54 changed files: 30 production, 24 tests, 0 docs,
  0 migration, 0 frontend): no blocker found; all 14 Slice B acceptance
  items verified PASS with direct evidence.

**SAFE CONFIGURATION CHANGES -- SLICE B ACCEPTANCE VERIFIED, NOT YET
COMMITTED -- the draft configuration lifecycle (begin/clone/discard,
draft-first reads/writes, stale-timetable mutation guard) is complete
and fully verified in the working tree, pending commit. Safe
post-generation configuration EDITING is now possible end-to-end for
every configuration entity through existing CRUD APIs while a draft is
open. Regeneration after a configuration edit (Slice C) is explicitly
NOT started -- there is still no way to re-solve a schedule against an
edited draft and publish it as a new revision; a discarded draft simply
reverts to the unchanged published schedule.**

## SAFE CONFIGURATION CHANGES -- SLICE C -- SAFE REGENERATION BACKEND CLOSED

Slice C adds a separate regeneration lifecycle at `POST
/schools/{school_id}/years/{year_id}/schedule/active/regenerate`; the
existing `/schedule/generate` endpoint remains initial-generation-only.
Regeneration requires an open configuration draft and `base_version_number`.
The service loads the exact internal ConfigurationRevision row identity
together with its detached SchedulingProblem snapshot, solves without a
database transaction open, and persistence rechecks both identity and
configuration content under the AcademicYear row lock before publishing.

Compatible historical locks remain hard solver pins and are carried to the
new version. Incompatible locks require an exact natural-ID confirmation
set; confirmed incompatible locks are dropped. REQUIRED compatibility uses
only resolved locked logical occurrences as an order-independent block-size
multiset subset; unlocked historical placements do not consume capacity.
FixedPlacement pins one lesson-period, so different slots for a
multi-period requirement are not automatically incompatible; only directly
provable conflicts are classified before the solver.

Successful regeneration atomically publishes the exact draft, clears its
pointer, creates and activates ScheduleVersion N+1 linked to that revision,
persists compatible locks, and preserves historical versions and published
revisions. Failures preserve the previous active/published state, draft, and
history. Edited-version natural-ID mappings are scoped to the active
version's configuration revision. No migration or frontend behavior is
included in this backend closure.

Verification: core tests 655 passed (5 deselected), real-PostgreSQL
`tests_web` 660 passed, focused Slice C/backend tests 199 passed, and
Alembic head/current `398b05641152` with no detected upgrade operations.

## SAFE CONFIGURATION CHANGES -- FRONTEND REGENERATION WORKFLOW CLOSED

The browser workflow for configuration drafts and schedule regeneration is
shipped. Configuration Setup owns opening and discarding an editable draft.
Timetable reads authoritative configuration-revision state and offers
Regenerate only when an active timetable is semantically out of date. An
untouched clone remains current; restoring a draft to the exact active-
version semantics also returns it to current.

`timetable_out_of_date` compares the complete draft `SchedulingProblem`
against the configuration revision referenced by the active
`ScheduleVersion` (`Schedule.active_version_id` ->
`ScheduleVersion.configuration_revision_id`). It is not inferred merely
from draft existence or from an arbitrary historical published revision,
and clone/revision surrogate identities do not participate in equality.
The read is serialized with configuration writes, lifecycle transitions,
publication, and active-version promotion through the shared
`AcademicYear SELECT ... FOR UPDATE` protocol.

Regeneration keeps the stale active timetable visible but read-only. It
never auto-retries state conflicts. Incompatible historical locks require
explicit confirmation of the exact natural-ID occurrence-key set returned
by the backend; compatible locks remain protected, and a changed
incompatible set requires a new explicit confirmation. Successful
regeneration atomically publishes the exact draft, clears the draft,
appends and activates a new schedule version, and refreshes timetable,
configuration state, and version history from their authoritative APIs.

Teacher Availability writes under an open draft are revision-scoped for
teacher, day, period, existing exception rows, ordinal allocation, and new
rows. This closes the published/draft natural-ID collision found during
browser acceptance.

Manual browser acceptance completed against `editing-review-school` /
`ay-editing-review-2026`: active Version 10 with an untouched Draft 2 was
current; changing Teacher Math, Monday, Period 1 from Available to Prefer
not made it stale; one Regenerate produced active Version 11 on published
revision 2, cleared the draft, and returned the timetable to current.
Version 10 remains immutable in append-only history on revision 1.

Closure verification: 167 focused PostgreSQL tests, 134 focused frontend
tests, 655 non-slow core tests (5 slow tests intentionally deselected), 666
full `tests_web` tests, and 604 full frontend tests passed; the frontend
production build succeeded. Alembic remains at the single head
`398b05641152`, with current at head and no upgrade operations detected.
Export remains future work and is not part of this slice.

## WHOLE-SCHOOL TEACHER TIMETABLE MATRIX CLOSED

The Timetable page now ships a read-only `Class | Teacher | Teacher Matrix`
view. A dedicated backend whole-school projection serves both the active
schedule version and an exact requested historical version, always resolving
display semantics from that version's own configuration revision. Its public
contract uses natural IDs, ordered configured dimensions, every configured
teacher (including zero-load teachers), and sparse occupied cells; absence of
a teacher/day/period coordinate means free. Days and instructional periods are
data-driven, and noninstructional periods are excluded.

The compact native-web matrix groups configured days over configured periods,
keeps teacher and header cells sticky, scrolls horizontally, and presents
period positions as Roman numerals without changing configured Period IDs,
names, or indexes. `WHOLE_CLASS` cells show configured class-section names;
`MERGED_CLASSES` cells show configured class-section names joined by ` + `;
`SUBGROUP` cells retain the authoritative participant-group name; targetless
reserved/special activities fall back to their activity name. These rules use
structured entry semantics and never parse participant-group display text.

The earlier spreadsheet image was a visual/layout reference only. No class
range, day count, period count, color scheme, notation, or spreadsheet data
model was copied. Excel/PDF export remains outside this feature.

Closure verification: 27 focused Matrix/Teacher application tests, 5 focused
PostgreSQL Matrix API tests with zero skips, 663 non-slow core tests (5 slow
tests intentionally deselected), 671 full `tests_web` tests with zero skips,
and 627 frontend tests passed; the production frontend build succeeded.
Alembic remains at the single head `398b05641152`, current at head, with no
new upgrade operations or Matrix migration.
