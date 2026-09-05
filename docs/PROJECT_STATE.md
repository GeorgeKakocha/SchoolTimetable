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
component). **Phase 3B.1 is the next authorized slice only**
(backend class-timetable projection: application read/view model,
projection service, the new endpoint, and its tests) -- **3B.2/3B.3/3B.4
must not start early.**

After Phase 3A3 (3A3.1-3A3.4) closes, the roadmap continues:

**Phase 3B -- React/TypeScript first visual timetable**: a real
generated/persisted 5x8 class timetable rendered in the browser, via a
dedicated backend class-timetable projection endpoint (`DECISIONS.md`
#32) consumed by React -- not a client-side projection over
`GET .../schedule/active` (and `/config`) directly.

Roadmap: **3A2.4 -> 3A3 -> 3B (3B.1 -> 3B.2 -> 3B.3 -> 3B.4).**
