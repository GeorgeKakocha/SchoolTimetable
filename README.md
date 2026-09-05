# School Timetable

A configurable school timetable optimization system. The solver/domain
core (this section) is an isolated Python package with no web/database
dependency at all -- see `docs/` for the full product spec, architecture,
decisions, and solver contract. A FastAPI/PostgreSQL backend foundation
is being built alongside it starting Phase 3A (see "Web/database
foundation" below); it never becomes a dependency of the solver core.

## Environment setup

Requires Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

## Installation

```bash
pip install -e ".[test]"
```

This installs the runtime dependency (`ortools`) and the test dependency
(`pytest`) -- everything needed to run the solver, the demos below, and
the full `tests/` suite. No web framework or database driver is pulled in
by this install; see "Web/database foundation" for that.

## Running the tests

```bash
pytest -q
```

Heavier, solver-invoking scale tests are marked `slow` and excluded by
default. Run them explicitly with:

```bash
pytest -q -m slow
```

## Running the PoC

Solves the deterministic synthetic fixture, runs the independent
verifier, and prints a weekly grid per class:

```bash
python -m school_timetable.run_poc
```

## Running the school-scale benchmark

Solves the realistic 15-class synthetic school (see
`docs/SCALE_VALIDATION.md`) and reports compact per-run statistics:

```bash
python -m school_timetable.run_scale_benchmark
python -m school_timetable.run_scale_benchmark --scenario standard --repeats 3
python -m school_timetable.run_scale_benchmark --detail standard
```

## Running the editing/re-optimization demo

Solves the Phase-1 fixture, validates and applies a manual move, locks
the moved lesson, introduces a new constraint, and re-optimizes (see
`docs/SCHEDULE_EDITING.md`):

```bash
python -m school_timetable.run_editing_demo
```

## Web/database foundation (Phase 3A1)

Optional and additive: the FastAPI app and PostgreSQL persistence layer
live entirely outside `domain/`/`scheduling/`/`validation/`/`verification/`,
which remain importable and testable with zero web dependencies (as
verified above). Nothing here has any domain/business endpoints yet --
Phase 3A1 is infrastructure only: config, a database engine/session,
Alembic wiring, and a real (not faked) database-backed health check.

Install the extra dependencies:

```bash
pip install -e ".[web]"
```

Start a local PostgreSQL 16 (development database, plus a separate
`school_timetable_test` database for the web/persistence test suite,
created automatically the first time the container's data volume is
initialized):

```bash
docker compose up -d db
```

Configure the app (copy and adjust if your setup differs from
docker-compose's defaults):

```bash
cp .env.example .env
```

Apply migrations (Phase 3A1 ships one empty baseline revision -- no
domain tables exist yet):

```bash
alembic upgrade head
```

Run the API:

```bash
uvicorn school_timetable.api.main:app --reload
```

```bash
curl http://localhost:8000/health
# {"status": "ok", "database": "ok"}            -- when PostgreSQL is reachable
# {"status": "error", "database": "unreachable"} -- 503, when it is not (body is
#   deliberately generic: no connection string, host, or exception detail --
#   see docs/DECISIONS.md)
```

Run the web/persistence test suite (separate from `tests/`; needs the
`web` extras and, for the database-backed tests, a reachable PostgreSQL
-- tests that need one skip cleanly, rather than fail, if it isn't
running):

```bash
pytest -q tests_web
```

`pytest -q` (no arguments) never runs `tests_web/` -- `pyproject.toml`'s
`testpaths` points only at `tests/`, so the pure domain/solver suite's
dependency-free, sub-2-second run is unaffected by any of this.

## Project layout

```
src/school_timetable/
├── domain/         Typed domain model (no OR-Tools, no I/O), including Schedule/OccurrenceKey
├── validation/      Preflight validation
├── scheduling/       CP-SAT model building, solving, weights, SolverOptions,
│                     manual editing (editing.py), re-optimization (reoptimize.py)
├── verification/     Independent post-hoc verifier
├── fixtures/         Deterministic synthetic fixtures
│   └── school_scale/  Realistic school-scale fixture generator (Phase 2B)
├── config.py         Web/persistence-layer settings (DATABASE_URL, etc.) -- Phase 3
├── persistence/       SQLAlchemy engine/session + Alembic migrations -- Phase 3
│   └── migrations/     Alembic environment + revisions (one empty baseline so far)
├── api/                FastAPI app shell + GET /health -- Phase 3
├── run_poc.py        Manual end-to-end demo (small Phase-1 fixture)
├── run_scale_benchmark.py  School-scale benchmark runner (Phase 2B)
└── run_editing_demo.py     Manual move / lock / re-optimize walkthrough (Phase 2C)
tests/                pytest suite (domain/solver core; no web/DB dependency)
tests_web/            Web/persistence test suite (Phase 3; needs `web` extras + PostgreSQL)
docker-compose.yml    Local development PostgreSQL 16
docs/                 Product/architecture/decisions/solver-contract/scale-validation/schedule-editing docs
```
