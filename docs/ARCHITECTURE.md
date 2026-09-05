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
(`8cdd513e16da`). Phase 3A2.2 adds `persistence/mappers.py`: pure
persistence -> domain mapping functions plus a `NaturalIdLookup`
context, with no schema/migration change. No `application/` repository
Protocol, no domain -> persistence write path, and no config-read API
exist yet -- see `docs/PROJECT_STATE.md` and `DECISIONS.md` #26-28 for
exactly what Phase 3A2.1/3A2.2 do and do not include.

```
src/school_timetable/
├── domain/         Pure Python domain model. No OR-Tools, no I/O.
├── validation/      Preflight validation (runs before CP-SAT).
├── scheduling/       CP-SAT model building, solving, weights, result conversion.
├── verification/     Independent post-hoc verifier (does not trust CP-SAT).
├── fixtures/         Deterministic synthetic test fixtures.
├── config.py         Web/persistence settings (Phase 3). Never imported by the four packages above.
├── persistence/       SQLAlchemy engine/session + Alembic + ORM models + mappers (Phase 3). No repository/writer yet.
└── api/                FastAPI app shell (Phase 3). No domain endpoints yet.
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
  `persistence/`, or (once it exists) `application/` -- fixtures are
  test/demo-only and must never become part of the real product path.
- **config.py** (Phase 3): the only place `DATABASE_URL` (or any other
  web/persistence-only setting) is read from the environment. Not
  imported by `domain/`, `scheduling/`, `validation/`, or `verification/`.
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
  No repository adapter exists yet (Phase 3A2.3+). Never imports
  `scheduling/` or `api/`.
- **api/** (Phase 3): the FastAPI app (`main.py`). No domain/business
  endpoints yet -- just `GET /health`, which genuinely executes `SELECT 1`
  against the database (returning 503 with a generic, non-sensitive body
  if unreachable -- see `DECISIONS.md` #25 -- never a faked 200).

## Locked direction: ports and adapters (from Phase 3A2 onward)

Starting with the first real persistence use case (Phase 3A2, not yet
implemented), the dependency direction is:

```
api/  →  application/ (services)  →  repository ports/interfaces
                                            ↑
                                     persistence/ adapters

application/  →  domain/, scheduling/
```

`application/` depends only on repository *interfaces* it defines itself
(as `typing.Protocol`s, not ABCs -- no inheritance requirement, matching
this codebase's plain-dataclass minimalism), plus `domain/`/`scheduling/`
for the actual scheduling behavior. It never imports `persistence/` or
`sqlalchemy`, even transitively. `persistence/` implements those
interfaces against a real `Session`, converting to/from `domain/` objects
-- concrete SQLAlchemy ORM models are deliberately **separate classes**
from the frozen `domain/` dataclasses, mapped explicitly, never the same
classes (`domain/` staying frozen/immutable is load-bearing for Phase 2C's
editing/re-optimization safety guarantees, which ORM change-tracking
would fight). The composition root wiring a concrete adapter into a
service (e.g. via FastAPI `Depends`) lives in `api/` and is the only place
that imports both `application/` and `persistence/` together. Repository
Protocols are intentionally not created ahead of the first concrete use
case that needs them -- see `DECISIONS.md`.

## Why isolate the solver like this

A schedule that violates a hard constraint is a bug, not a partial
success. Keeping preflight, model-building, and verification as separate,
independently testable modules means a bug in one layer cannot silently
paper over a bug in another -- in particular, the independent verifier is
the safety net if the CP-SAT encoding itself has a modeling defect (this
happened once during this milestone's development; see `PROJECT_STATE.md`).

## What does not exist yet

As of Phase 3A2.2: no `application/` layer, no repository ports/adapters,
no domain -> persistence write path, no full `SchedulingProblem`
aggregate loader, no business/domain REST endpoints (including no
config-read API yet), no frontend, no auth. `docker-compose.yml`
provides a local development PostgreSQL only -- no application
containerization/deployment setup exists yet. These arrive in later
Phase 3 slices per `DECISIONS.md` and `PROJECT_STATE.md`.
