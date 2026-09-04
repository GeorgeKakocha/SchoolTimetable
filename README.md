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

## Running the PoC

Solves the deterministic synthetic fixture, runs the independent
verifier, and prints a weekly grid per class:

```bash
python -m school_timetable.run_poc
```

## Project layout

```
src/school_timetable/
├── domain/         Typed domain model (no OR-Tools, no I/O)
├── validation/      Preflight validation
├── scheduling/       CP-SAT model building, solving, weights
├── verification/     Independent post-hoc verifier
├── fixtures/         Deterministic synthetic fixtures
└── run_poc.py        Manual end-to-end demo
tests/                pytest suite
docs/                 Product/architecture/decisions/solver-contract docs
```
