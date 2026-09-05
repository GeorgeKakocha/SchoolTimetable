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

**Phase 3A3 (design locked; schema merged, remainder not yet
implemented)** connects this DB-backed `SchedulingProblem` to the
existing solver and persists generated results as immutable
`ScheduleVersion` snapshots -- see `DECISIONS.md` #31 for the full
locked schema, application-service, and API design. One canonical
`Schedule` per School+AcademicYear; generation is
initial-generation-only (a second `Generate` call conflicts, 409,
rather than reoptimizing or appending); a new `GenerateScheduleService`
in `application/` orchestrates load -> preflight -> solve -> require
success -> verify -> persist, using one new application-owned port,
`ScheduleVersionRepository`. Phase 3A3.1's `schedule`/`schedule_version`/
`schedule_entry`/`locked_occurrence` ORM models and Alembic migration
(`4681f7a362bd`) are merged to `main`; no repository adapter,
application service, or API route exists yet -- Phase 3A3.2 (mappers +
repository adapter) is the next implementation
slice, not started.

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
- **application/** (Phase 3A2.3): repository ports/interfaces
  `persistence/` adapters implement, plus the errors those ports raise
  -- `ports.py`'s `SchedulingProblemRepository` (one method,
  `load_by_school_and_year`, no generic CRUD) and `errors.py`'s
  `SchedulingProblemNotFoundError`. Imports only `domain/`; never
  SQLAlchemy, `persistence/`, FastAPI, or `fixtures/`, even
  transitively -- see `DECISIONS.md` #29.
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
  imports `scheduling/`, `api/`, or `fixtures/`.
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
  concrete repository adapter -- see `DECISIONS.md` #30.

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

As of Phase 3A3.1 (closed and merged to `main`): no domain ->
persistence write path in production code, no `GenerateScheduleService`
or `ScheduleVersionRepository`, no domain <-> persistence mapping or
repository adapter for the new schedule tables, no
`POST .../schedule/generate` or `GET .../schedule/active` endpoint, no
application service layer of any
kind yet, no frontend, no auth. `docker-compose.yml` provides a local
development PostgreSQL only -- no application
containerization/deployment setup exists yet. These arrive starting
Phase 3A3.1 per `DECISIONS.md` #31 and `PROJECT_STATE.md`.
