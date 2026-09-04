# Product Spec

## What this is

School Timetable is a configurable school timetable optimization system.
Given a school's configuration and teaching data, it produces a weekly
timetable that respects hard constraints and optimizes soft preferences.

## Configurable-school philosophy

The system is **not** hard-coded for any specific school. Every school
supplies its own:

- academic year, school days, lesson periods
- teachers and their availability
- classes and participant groups
- subjects / ordinary activities
- teaching requirements (who teaches what, to whom, how often)
- weekly lesson counts, lesson block rules, distribution rules
- club/reserved blocks, split groups, merged groups
- time preferences, resources, fixed/locked placements

No solver logic assumes a specific number of days, periods, teachers, or
subjects -- these always come from the input `SchedulingProblem`.

## What the solver decides (and does not decide)

The school already knows **who teaches what to whom, how many times a
week** -- that is a `TeachingRequirement`. The solver's only job is to
decide **when** each required lesson happens, subject to the hard/soft
rules in `SOLVER_CONTRACT.md`.

## Current milestone: solver proof-of-concept

This first milestone is an isolated Python solver PoC. It intentionally
excludes: FastAPI, a database, SQLAlchemy/Alembic, PostgreSQL, React/TS,
authentication, Docker, and deployment. Those arrive in later milestones,
built around this same domain model and solver contract.
