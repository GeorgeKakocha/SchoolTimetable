# Architecture

## Long-term direction

- **Backend**: Python, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL
- **Solver**: Google OR-Tools CP-SAT
- **Frontend**: React, TypeScript, Vite
- **Shape**: modular monolith, with scheduling/domain logic isolated from
  API/UI/infrastructure. The solver must be independently executable and
  testable without any of the above.

## Current milestone

The solver core (`domain/`, `scheduling/`, `validation/`, `verification/`,
`fixtures/`) is unchanged since Phase 2C and remains fully independent:
no web framework, no database, no UI dependency, importable and testable
on its own. Phase 3A1 added the web/persistence foundation *alongside*
it (config, a database engine/session, Alembic migration wiring, a
FastAPI app shell with a real database-backed health check). Phase 3A2.1
added the first real persisted schema on top of that foundation: 17
SQLAlchemy ORM tables (`persistence/models.py`) covering the complete
current `SchedulingProblem` input surface, one Alembic migration
(`8cdd513e16da`). Phase 3A2.2 added `persistence/mappers.py`: pure
persistence -> domain mapping functions plus a `NaturalIdLookup`
context, with no schema/migration change. Phase 3A2.3 adds the first
real `application/` package (`ports.py`'s `SchedulingProblemRepository`
Protocol, `errors.py`'s `SchedulingProblemNotFoundError`) and its
concrete adapter, `persistence/problem_repository.py`'s
`SqlAlchemySchedulingProblemRepository` -- proven end-to-end against
real PostgreSQL via a TEST-ONLY aggregate writer
(`tests_web/support/problem_writer.py`). Phase 3A2.4 adds the first
domain/business endpoint, `GET /schools/{school_id}/years/{year_id}/config`
(read-only): `api/schemas.py`'s hand-designed Pydantic response,
`api/serializer.py`'s pure domain -> API mapper, and `api/dependencies.py`
as the composition root wiring the Phase 3A2.3 repository into the
route. Still no domain -> persistence write path in production code and
no solver/schedule-generation endpoint -- see `docs/PROJECT_STATE.md`
and `DECISIONS.md` #26-30 for exactly what Phase
3A2.1/3A2.2/3A2.3/3A2.4 do and do not include.

**Phase 3A3 (design locked; schema, persistence mapping/adapter,
application orchestration, and the HTTP API all merged -- CLOSED)**
connects this DB-backed `SchedulingProblem` to the existing solver and
persists generated results as immutable `ScheduleVersion` snapshots --
see `DECISIONS.md` #31 for the full locked schema, application-service,
and API design. One canonical `Schedule` per School+AcademicYear;
generation is initial-generation-only (a second `Generate` call
conflicts, 409, rather than reoptimizing or appending). Phase 3A3.1's
`schedule`/`schedule_version`/`schedule_entry`/`locked_occurrence` ORM
models and Alembic migration (`4681f7a362bd`) are merged to `main`.
Phase 3A3.2 (schedule persistence mapping, `ScheduleVersionRepository` +
its `SqlAlchemyScheduleVersionRepository` adapter, and a
session-factory-backed `SchedulingProblemRepository` implementation for
generation use) is likewise merged to `main`. Phase 3A3.3's
`application.generate_schedule_service.GenerateScheduleService` is
likewise merged to `main`. Phase 3A3.4 (`api/schedule_routes.py`'s `POST
/schools/{school_id}/years/{year_id}/schedule/generate`/
`GET /schools/{school_id}/years/{year_id}/schedule/active`, their
response schemas/serializers, and the composition-root wiring in
`api/dependencies.py`) is likewise merged to `main` -- both are real
production routes, exactly matching the contract fully locked (zero
remaining owner decisions) in `DECISIONS.md` #31; see
`PROJECT_STATE.md` for the full implemented-contract summary.
Generation composition still preserves the no-request-scoped-session-
across-solve boundary (`get_schedule_version_repository`/
`get_generate_schedule_service` are session-factory-backed against
`SessionLocal`, never `Depends(get_session)`); `/config`'s existing
request-scoped read path is unchanged. The schedule read API remains
generic and flat, not a Phase 3B 5x8 React projection.

**Phase 3B (first-view design locked; 3B.1 backend projection, 3B.2
frontend foundation, 3B.3 live class timetable, and 3B.4 UX hardening
all merged to `main` -- 3B.3 at commit `831c900`, 3B.4 at commit
`7c80eba`; Phase 3B is CLOSED)** adds the
first browser-rendered class timetable, per `DECISIONS.md` #32. The
active data path is: persisted `ScheduleVersion` -> backend
application-layer class projection -> the read-only class projection
API -> the frontend's native `fetch` client -> React
(`App`/`ClassSelector`/`TimetableGrid`). The data flow is locked as: flat persisted schedule +
`SchedulingProblem`/config -> a backend **application-layer**
projection -> a UI-shaped, read-only API response -> React renders the
already-correct projection. React may consume the backend projection
but does NOT recreate class membership, timetable grouping,
parallel-entry cardinality, or calendar ordering semantics -- one
`ClassSection`/day/period can genuinely hold more than one simultaneous
`ScheduleEntry` (parallel split-`ParticipantGroup` branches, remaining
zero-or-more per cell), so that grouping/cardinality logic stays in the
backend application layer, in
`application.class_timetable_service.ClassTimetableService` -- never in
`React`, ORM models, repository SQL, or `api/serializer.py` treated as
ad-hoc business logic. The route,
`GET .../schedule/active/classes/{class_section_id}`
(`api/schedule_routes.py`), is live on `main`, read-only, and consumes
the existing `SchedulingProblemRepository`/`ScheduleVersionRepository`
ports reused entirely unchanged -- no repository method, persistence,
solver, or verifier redesign was needed.

`frontend/` (React 19, TypeScript 7, Vite 8, no router/state-management/
component library) is on `main` with a real, live-rendering class
timetable: `ClassSelector` (`bff1c31` foundation, `831c900` live
timetable) and `TimetableGrid` + orchestrating `App.tsx` (both added by
`831c900`) implement class selection, class switching, and the
loading/config-error/zero-class/no-schedule/timetable-error UI states.
The live DB -> backend API -> Vite proxy path has been proven against a
real locally generated schedule through this same boundary; React
rendering of that data is covered by automated frontend tests, and has
also been manually confirmed against the real running app in a browser
(including the German/Russian same-cell split) -- see
`PROJECT_STATE.md` for the full proof and manual review record. The
local development boundary is locked as: frontend
native `fetch` client -> a relative `/schools/...` URL -> the Vite dev
proxy (`vite.config.ts`) -> `http://127.0.0.1:8000`. That Vite proxy
configuration is the one and only allowed place an absolute local
backend address appears; the frontend's own API-client/component
request-construction code (`src/api/client.ts`) uses relative URLs
exclusively; no FastAPI `CORSMiddleware` was added. See `DECISIONS.md`
#32 for the complete locked first-slice scope, cell-cardinality/display
rules, calendar-derivation policy, and the 3B.1-3B.4 sub-slice
sequence.

```
src/school_timetable/
├── domain/         Pure Python domain model. No OR-Tools, no I/O.
├── validation/      Preflight validation (runs before CP-SAT).
├── scheduling/       CP-SAT model building, solving, weights, result conversion.
├── verification/     Independent post-hoc verifier (does not trust CP-SAT).
├── fixtures/         Deterministic synthetic test fixtures.
├── config.py         Web/persistence settings (Phase 3). Never imported by the four packages above.
├── application/       Repository ports + errors (Phase 3). Imports domain/ only.
├── persistence/       SQLAlchemy engine/session + Alembic + ORM models + mappers + repository adapter (Phase 3).
└── api/                FastAPI app: health check + read-only config endpoint + composition root (Phase 3).
```

## Module boundaries

- **domain/**: typed dataclasses/enums describing the scheduling problem
  and its result. Nothing here imports `ortools`. This is the shared
  vocabulary for validation, solving, and verification.
- **validation/**: `run_preflight(problem) -> list[ValidationError]`.
  Reasons purely about domain objects; never inspects a CP-SAT model.
- **scheduling/**: the only package allowed to import `ortools`.
  - `model_builder.py` turns a `SchedulingProblem` into CP-SAT
    variables/constraints/objective (a `BuiltModel`).
  - `weights.py` centralizes soft-constraint penalty weights.
  - `result_builder.py` converts a solved model back into domain
    `ScheduleEntry` objects.
  - `solver.py` is the public `solve(problem) -> SchedulingResult`
    entry point, orchestrating preflight -> build -> solve -> convert.
- **verification/**: `verify(problem, entries) -> VerificationReport`,
  re-derived from scratch by scanning the final schedule -- it never reads
  CP-SAT variables or trusts the solver's own claim of feasibility.
- **fixtures/**: synthetic, deterministic problem builders used by tests
  and manual runs. No real school data. Never imported by `api/`,
  `persistence/`, or `application/` -- fixtures are test/demo-only and
  must never become part of the real product path. (Phase 3A2.3's
  TEST-ONLY aggregate writer, `tests_web/support/problem_writer.py`,
  imports fixtures only from test code that calls it, never itself.)
- **config.py** (Phase 3): the only place `DATABASE_URL` (or any other
  web/persistence-only setting) is read from the environment. Not
  imported by `domain/`, `scheduling/`, `validation/`, or `verification/`.
- **application/** (Phase 3A2.3, extended Phase 3A3.2/3A3.3): repository
  ports/interfaces `persistence/` adapters implement, the errors those
  ports (and the orchestration service below) raise, the plain
  read-model dataclasses those ports return, and the first genuine
  orchestration service -- `ports.py`'s `SchedulingProblemRepository`
  (`load_by_school_and_year`) and `ScheduleVersionRepository`
  (`get_active_schedule`/`persist_initial_version`, no generic CRUD),
  `errors.py`'s `SchedulingProblemNotFoundError`/
  `ScheduleAlreadyExistsError`/`InvalidSchedulingConfigurationError`/
  `ScheduleInfeasibleError`, `schedule_models.py`'s
  `ActiveScheduleVersion`, and (Phase 3A3.3)
  `generate_schedule_service.py`'s `GenerateScheduleService`
  (load -> preflight -> solve -> verify -> persist, plus the
  internal-only `ScheduleGenerationError`/`ScheduleVerificationFailedError`
  defects it raises, never a public outcome). Imports only `domain/`,
  `validation/`, `scheduling/`, and `verification/`; never SQLAlchemy,
  `persistence/`, FastAPI, or `fixtures/`, even transitively -- see
  `DECISIONS.md` #29.
- **persistence/** (Phase 3): SQLAlchemy engine/session construction
  (`db.py`), the declarative `Base` (`base.py`), and (Phase 3A2.1) the
  ORM models themselves (`models.py`) -- 17 tables persisting the
  complete current `SchedulingProblem` input surface as one
  `academic_year_id`-scoped configuration snapshot, with same-year
  composite-FK isolation, typed (never JSONB) policy columns, and
  exact tuple-order preservation; see `DECISIONS.md` #26 for the full
  locked schema rules. `persistence/__init__.py` imports `models` so
  that importing `persistence.base.Base` (as `migrations/env.py` does)
  always sees every table -- this is what keeps
  `alembic revision --autogenerate` picking up new models automatically
  with no further `env.py` changes. (Phase 3A2.2) `mappers.py`: pure
  persistence -> domain mapping functions (no `Session`, no query, no
  `fixtures/` import) plus a `NaturalIdLookup` context resolving
  sibling surrogate FKs to natural IDs; domain -> persistence is
  intentionally not implemented here (see `DECISIONS.md` #28 for why).
  (Phase 3A2.3) `problem_repository.py`'s
  `SqlAlchemySchedulingProblemRepository`: implements
  `application.ports.SchedulingProblemRepository` structurally (no
  inheritance) via explicit multi-`SELECT` loading (no ORM
  `relationship()`/lazy-loading), reimplementing no
  preflight/solver/verifier reasoning -- see `DECISIONS.md` #29. Never
  imports `scheduling/`, `api/`, or `fixtures/`. (Phase 3A3.2)
  `mappers.py` additionally gains `schedule_entry_to_domain`/
  `locked_occurrence_to_domain`, re-deriving each entry's denormalized
  fields from configuration already loaded, per `DECISIONS.md` #31;
  `problem_repository.py` additionally gains
  `SessionFactorySchedulingProblemRepository`, a second,
  session-factory-backed implementation of the same
  `SchedulingProblemRepository` Protocol for future generation use,
  which does not change `SqlAlchemySchedulingProblemRepository`'s
  existing session-bound `/config` read path; and the new
  `schedule_repository.py`'s `SqlAlchemyScheduleVersionRepository`
  implements `application.ports.ScheduleVersionRepository`, likewise
  session-factory-backed, atomically writing `Schedule` +
  `ScheduleVersion` + `ScheduleEntry` rows and translating only a
  by-name `uq_schedule_academic_year_id` violation into
  `ScheduleAlreadyExistsError`.
- **api/** (Phase 3): the FastAPI app (`main.py`). `GET /health`
  genuinely executes `SELECT 1` against the database (returning 503
  with a generic, non-sensitive body if unreachable -- see
  `DECISIONS.md` #25 -- never a faked 200). (Phase 3A2.4) the first
  domain/business endpoint, `GET /schools/{school_id}/years/{year_id}/config`
  (`config_routes.py`) -- read-only, natural-ID path parameters, typed
  against `application.ports.SchedulingProblemRepository` (never the
  concrete adapter). `schemas.py`/`serializer.py` hand-design the public
  JSON contract explicitly (never `dataclasses.asdict()` or ORM-row
  serialization) and import no SQLAlchemy/`persistence.models`/
  `fixtures/`. `dependencies.py` is the one composition root importing
  both `application/` and `persistence/` together, wrapping the
  existing per-request `persistence.db.get_session` to construct the
  concrete repository adapter -- see `DECISIONS.md` #30. (Phase 3A3.4)
  `schedule_routes.py` adds `GET .../schedule/active` and
  `POST .../schedule/generate` (both typed against
  `application.ports.ScheduleVersionRepository`/
  `application.generate_schedule_service.GenerateScheduleService`, never
  a concrete adapter); `dependencies.py` gains
  `get_schedule_version_repository`/`get_generate_schedule_service`,
  both session-factory-backed against `persistence.db.SessionLocal`
  directly -- deliberately never `Depends(get_session)`, so generation
  never holds a request-scoped `Session` open across a solve (Decision
  #31 Owner Decision 4); `/config`'s own request-scoped dependency is
  unchanged.

## Locked direction: ports and adapters (implemented from Phase 3A2.3, wired to `api/` in Phase 3A2.4)

The dependency direction, now with its first real instance
(`SchedulingProblemRepository`, Phase 3A2.3) implemented:

```
api/  →  application/ (services)  →  repository ports/interfaces
                                            ↑
                                     persistence/ adapters

application/  →  domain/, scheduling/
```

`application/` depends only on repository *interfaces* it defines itself
(as `typing.Protocol`s, not ABCs -- no inheritance requirement, matching
this codebase's plain-dataclass minimalism), plus `domain/`/`scheduling/`
for the actual scheduling behavior (the concrete Phase 3A2.3 port only
exercises the `domain/` half of that so far -- no application service
combining `scheduling/` with a repository exists yet). It never imports
`persistence/` or `sqlalchemy`, even transitively. `persistence/`
implements those interfaces against a real `Session`, converting to/from
`domain/` objects -- concrete SQLAlchemy ORM models are deliberately
**separate classes** from the frozen `domain/` dataclasses, mapped
explicitly, never the same classes (`domain/` staying frozen/immutable
is load-bearing for Phase 2C's editing/re-optimization safety
guarantees, which ORM change-tracking would fight). The composition root
wiring a concrete adapter into a route (via FastAPI `Depends`) lives in
`api/dependencies.py` and is the only place that imports both
`application/` and `persistence/` together (Phase 3A2.4). No
application *service* layer exists yet -- the current read-only route
calls the repository port directly, since a pass-through use case does
not yet justify one (Decision #12); a real service is added only when a
genuine use case needs one, e.g. combining `scheduling/` with a
repository. Repository Protocols are created one at a time, alongside
the first concrete use case that needs each one, never as speculative
scaffolding
-- see `DECISIONS.md` #12, #29.

## Why isolate the solver like this

A schedule that violates a hard constraint is a bug, not a partial
success. Keeping preflight, model-building, and verification as separate,
independently testable modules means a bug in one layer cannot silently
paper over a bug in another -- in particular, the independent verifier is
the safety net if the CP-SAT encoding itself has a modeling defect (this
happened once during this milestone's development; see `PROJECT_STATE.md`).

## What does not exist yet

As of Phase 3B.3 (CLOSED, merged to `main` at commit `831c900`):
`POST .../schedule/generate`, `GET .../schedule/active`, and
`GET .../schedule/active/classes/{class_section_id}` are all real
production routes; the backend class-timetable projection
(`ClassTimetableService`/`ClassTimetableView`) exists on `main`; the
`frontend/` React/TypeScript/Vite foundation exists on `main`; and a
real `ClassSelector`, `TimetableGrid`, live backend timetable rendering
in React, class switching, and the loading/config-error/zero-class/
no-schedule/timetable-error UI states all exist on `main` too -- there
is no Generate button, by design. Phase 3B.4 visual/UX hardening
(narrower Period column, wider laptop-width layout, compact vertical
density, restrained Reserved-block treatment, accessibility polish) is
also merged to `main` at commit `7c80eba` -- **Phase 3B is CLOSED**.
Remaining out of scope, unstarted: a
teacher timetable view; a schedule history UI; broader admin UI
(manual editing, locks, reoptimization web workflows, scenarios); auth;
export/print; and deployment hardening beyond `docker-compose.yml`'s
local development PostgreSQL. See `PROJECT_STATE.md` for the full
Phase 3B.3/3B.4 proof and manual browser review record.

Scheduling-configuration admin input (formerly just a forward-looking
note) is now **Phase 3C, design locked at `DECISIONS.md` #33-#36; 3C.1
is CLOSED and merged to `main` at commit `8b5b606`; 3C.2a
(application/persistence backend + generation-vs-config-write
concurrency correctness) is implemented on branch
`feature/phase-3c2a-teaching-assignment-backend`, pending review/commit
-- not merged, not pushed; 3C.2b onward NOT started** -- see below.

## Phase 3C architecture direction

**3C.1's role contract is merged to `main` at commit `8b5b606`; 3C.2a
is implemented on a feature branch pending review; 3C.2b onward remains
design-locked, not implemented.**

Today, every one of the 17 configuration tables under one
`academic_year_id` (Decision #26) is fully readable via `GET /config`
but has **zero** production write path -- the only writer of any
configuration table is the TEST-ONLY aggregate writer
(`tests_web/support/problem_writer.py`), explicitly documented
(Decision #28) as never the template for a production write path. The
locked Phase 3C direction adds a real one, following the same
ports-and-adapters direction already in place:

```
api/  →  application/ (new write services)  →  new write ports
                                                       ↑
                                            persistence/ adapters (new)
```

- **`domain/`** (3C.1, implemented): `ParticipantGroup` gained a
  mandatory `role: ParticipantGroupRole` field (`WHOLE_CLASS`/
  `SUBGROUP`/`MERGED_CLASSES`, Decision #33) -- the dataclass stays a
  plain frozen data holder with no self-validation; role is
  **authoritative domain data**, supplied by every caller, never
  derived. `frozen` dataclasses remain frozen (no ORM change-tracking
  creeps into `domain/`).
- **`validation/`** (3C.1, implemented): `preflight.py` owns every
  structural/cross-row invariant for `role` -- per-role cardinality
  and the "exactly one `WHOLE_CLASS` per `ClassSection`" rule -- the
  single validation layer this codebase already channels all such
  rules through; nothing about `role` validation lives anywhere else.
- **`persistence/`** (3C.1, implemented): `ParticipantGroup.role` is
  `TEXT NOT NULL` + a row-local `CHECK` for the three values (matching
  the `Activity.kind`/`TeacherAvailability.status` convention) --
  migration `01b2ae564170`, the first schema change since Decision
  #26's original 17-table baseline, added with no `server_default` and
  no backfill (fail-closed by design). The "exactly one `WHOLE_CLASS`
  per `ClassSection`" cross-row invariant was evaluated for a
  denormalized-column/partial-unique-index treatment and **deferred**:
  `ParticipantGroup` has no write path yet (still read-only reference
  data through 3C.4), so a real DB structure defending against a write
  that can't currently happen would be premature; preflight is the
  sole enforcement point for now, revisited only if/when a future
  `ParticipantGroup` write service (3C.5+) needs it.
- **`api/`** (3C.1, implemented): `GET /config`'s `ParticipantGroupResponse`
  exposes `role` additively (`api/schemas.py`/`serializer.py`) -- no
  new route, no change to the composition root. No frontend code
  depends on `role` yet (the current frontend doesn't even mirror
  `participant_groups` from `/config`), confirmed by an unmodified,
  passing frontend test/build gate.
- **`application/`** (3C.2a, implemented on feature branch, pending
  review): `TeachingAssignmentService` -- a new, narrowly-scoped write
  use case (shaped like `GenerateScheduleService`, not a generic
  repository) for create/update/delete of **plain** `WHOLE_CLASS`
  `TeachingRequirement`s (Decision #34, predicate finalized/corrected
  in #36 to explicitly include "zero referencing `FixedPlacement`
  objects"), enforcing the Decision #35 schedule-exists write gate by
  reusing the existing `ScheduleVersionRepository.get_active_schedule`
  port method as a fast un-locked precheck -- no new repository method
  for that specific check. Pure validation rules live in
  `application/teaching_assignment_rules.py` (never SQLAlchemy), packaged
  as a `Callable[[SchedulingProblem], None]` so the identical logic runs
  both as the fast precheck and, again, as the authoritative recheck
  the new `TeachingAssignmentRepository` port invokes under its lock.
  New application-level errors (`TeachingAssignmentNotFoundError`,
  `NonWholeClassTargetError`, `AdvancedRequirementNotEditableError`,
  `DuplicateTeachingAssignmentError`, `ConfigurationLockedError`,
  `ConfigurationChangedDuringGenerationError`, `InvalidTeachingAssignmentError`,
  `UnknownReferenceError`) follow the existing
  `SchedulingProblemNotFoundError`/`ScheduleAlreadyExistsError` pattern
  (Decisions #29, #31), never a raw SQLAlchemy/HTTP exception.
- **`persistence/`** (3C.2a, implemented on feature branch, pending
  review): `SqlAlchemyTeachingAssignmentRepository`
  (`persistence/teaching_assignment_repository.py`) implements the new
  port -- every method opens its own short session (never held across a
  solve), takes a `SELECT ... FOR UPDATE` on the target `AcademicYear`
  row, reloads the current `SchedulingProblem` under that lock, and
  invokes the caller's `validate` callable against it before writing
  (Decision #36). `SqlAlchemyScheduleVersionRepository.persist_initial_version`
  (`schedule_repository.py`) now takes the exact `SchedulingProblem` the
  solver used as a new parameter and, immediately before its existing
  atomic Schedule/Version/Entry insert, acquires the identical
  `AcademicYear` row lock, reloads the current configuration, and
  compares it (frozen-dataclass structural equality) against that
  parameter -- a mismatch rolls back and raises
  `ConfigurationChangedDuringGenerationError` with nothing persisted; a
  match proceeds to its unchanged commit-while-locked behavior. No
  schema change was needed for any of this -- Alembic head is still
  `01b2ae564170`.
- **`api/`** write routes/schemas for 3C.2b (NOT implemented): composed
  the same way `dependencies.py` already composes read routes -- never
  a second composition root.
- **`frontend/`** (3C.3, NOT implemented): a second meaningful page
  (Teaching Assignments) will make Phase 3B's no-Router decision (#32
  Owner Decision 10, conditioned on there being only one page) worth
  revisiting; Redux/Zustand remain unjustified in the meantime. No
  `ParticipantGroup` CRUD/write UI exists yet.

See `DECISIONS.md` #33-#36 for the full locked rationale and
`PROJECT_STATE.md` for the 3C.1 implementation record and the
recommended 3C.1-3C.5 sequencing.
