# School Timetable

A configurable school timetable optimization system. This milestone is an
isolated Python solver proof-of-concept -- no web framework, database, or
UI yet. See `docs/` for the full product spec, architecture, decisions,
and solver contract.

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
(`pytest`).

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
├── run_poc.py        Manual end-to-end demo (small Phase-1 fixture)
├── run_scale_benchmark.py  School-scale benchmark runner (Phase 2B)
└── run_editing_demo.py     Manual move / lock / re-optimize walkthrough (Phase 2C)
tests/                pytest suite
docs/                 Product/architecture/decisions/solver-contract/scale-validation/schedule-editing docs
```
