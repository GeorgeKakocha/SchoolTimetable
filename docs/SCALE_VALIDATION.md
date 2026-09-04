# Scale Validation (Phase 2B)

This document records how the Phase 1/2A solver was validated at a
realistic school-like scale, the methodology used to guarantee the test
fixture is genuinely solvable, and the actual results observed on the
machine this work was done on.

**All timings in this document are local-machine measurements, not a
universal performance guarantee.** They were produced on a single
development machine under normal load; a CI runner, a different CPU, or a
loaded machine will produce different (likely slower) numbers. What is
portable is the *shape* of the result: solved in low single-digit
seconds, far under the 60s budget, with zero verifier violations.

## Fixture shape

`school_timetable.fixtures.school_scale` builds a deterministic,
synthetic-identifier-only school:

| Dimension | Value |
|---|---|
| School days | 5 |
| Instructional periods/day | 8 (lunch boundary between P4/P5, same as Phase 1) |
| Main classes | 15 (`class_01`..`class_15`) |
| Teachers | 34 (`teacher_01`..`teacher_34`) |
| Activities (subjects + clubs) | 19 (16 ordinary + 3 clubs) |
| TeachingRequirements | 227 |
| Participant groups | 28 |
| Total class-slot occupancy units/week | 600 (15 classes x 40 slots) |
| Split groups | 5 (classes 1-5, German/Russian) |
| Merged-lesson requirements | 3 (civics, 3 class-pairs) |
| Reserved/club cohorts | 3, each a different class group and a different day/period |
| Fixed placements | 8 (standard) / 14 (tight) |

Every one of the "required realistic features" in the Phase 2B brief is
present: full-time and part-time teachers, whole-day and partial-period
UNAVAILABLE slots, PREFER_NOT slots, `max_periods_per_day`, high-load
teachers (up to 20 periods/week), REQUIRED `[2,2]`, `[2,1,1,1]`, and
`[3,1]` patterns, PREFERRED double lessons, FLEXIBLE requirements, whole-
class and split and merged participant groups, capacity-1 gym demand
from both Sport and Dance, several `TimePreference` and
`min_distinct_days` rules, and 8+ fixed placements spread across
different days.

## Methodology: known-feasibility without leaking placements

A naive approach -- hand-writing ~230 `TeachingRequirement`s and hoping
CP-SAT can satisfy them all -- was explicitly out of scope. Instead:

1. **Curriculum first, placement never.** `curriculum.py` decides the
   entire school's offering (classes, teachers, subjects, weekly periods,
   block patterns, splits, merges, clubs, resources, availability) as
   plain data. Nothing about *when* anything happens is decided here.
2. **Build the problem with zero fixed placements.**
   `assemble.build_problem_from_curriculum(curriculum)` turns that
   curriculum into a `SchedulingProblem` with an empty
   `fixed_placements` tuple.
3. **Prove feasibility with one witness solve.**
   `constructor.build_canonical_schedule(problem)` runs the *exact same,
   already-validated* CP-SAT model builder and solver from Phases 1-2A
   against that placement-free problem, with a generous internal time
   budget (120s) and a fixed random seed. If CP-SAT does not return
   OPTIMAL/FEASIBLE, `FixtureGenerationError` is raised immediately --
   the fixture is never shipped as "known feasible" on faith.
4. **Promote a handful of witness placements to real fixed lessons.**
   8 (or 14, for the tight scenario) specific `(requirement, slot)` pairs
   from that witness are turned into genuine `FixedPlacement` objects --
   exactly the "deliberately configured fixed lessons" the brief allows.
   The rest of the witness is discarded.
5. **Rebuild the final problem with those fixed placements added,** and
   independently re-check full class occupancy
   (`consistency.check_class_occupancy_consistency`) before calling it
   done.

### Why CP-SAT for the witness, not a hand-rolled greedy

A hand-written greedy constructor was tried first: place multi-period
blocks before singles, prioritize scarce teachers, and so on. It
repeatedly dead-ended -- not because the curriculum was infeasible, but
because a single-pass greedy on a **zero-slack** problem (every class's
40 slots are exactly used, by design) can commit to an early choice that
blocks a later placement, even though a valid global arrangement exists.
Three different targeted heuristic fixes (processing order by teacher
load/availability pressure, prioritizing joint split/merge constraints,
seeded reshuffle-and-retry) each fixed one failure mode and immediately
exposed another. This is precisely the kind of problem CP-SAT's real
search exists to solve, so the witness-construction step was switched to
use it directly.

**This does not make the benchmark circular.** The witness solve (step 3
above) and the benchmark solve (the one actually measured, see below) are
two separate `CpSolver` invocations against two different
`SchedulingProblem`s (the second has 8-14 more `FixedPlacement`s than the
first). No decision the witness solver made about *which slot* to use for
any non-fixed lesson crosses into the benchmark run -- only the small,
explicit set of promoted fixed placements does, and those are legitimate
declared input, identical in kind to Phase 1's single fixed lesson.

## Scenarios

- **`school_scale_standard`**: the fixture as described above.
- **`school_scale_tight`**: the identical curriculum, with `extra_unavailability=True`
  (roughly a quarter of non-part-time teachers additionally lose one
  afternoon) and roughly twice as many fixed placements (14 vs 8). Proven
  feasible by the exact same witness-construction mechanism.
- **`school_scale_impossible`**: takes the already-proven-feasible
  standard `SchedulingProblem` and doubles every Sport/Dance
  requirement's `weekly_periods` (pushing capacity-1 gym demand from 30
  to 60 weekly periods against a 40-slot ceiling), trimming an equal
  amount from each affected class's Art requirement so occupancy and
  every reference stay internally consistent. This isolates a single,
  genuine, unavoidable resource bottleneck rather than an input error --
  it is expected to (and does) pass preflight and be caught only by
  CP-SAT as `INFEASIBLE`, exactly as the brief anticipates ("preflight
  does not attempt to prove resource feasibility ahead of time").

## Results (this machine, this run)

Machine: local development container, `ortools` 9.15, Python 3.12.3.
Solver options: `max_time_seconds=60, num_search_workers=8`.

### Standard (3 runs, different seeds)

| Run | Status | Penalty | Wall time (s) | Branches | Conflicts | Verifier |
|---|---|---|---|---|---|---|
| 1 | OPTIMAL | 0 | 1.00 | 3,400 | 0 | passed |
| 2 | OPTIMAL | 0 | 1.35 | 17,607 | 1 | passed |
| 3 | OPTIMAL | 0 | 1.34 | 2,320 | 0 | passed |

All three runs: OPTIMAL, zero soft penalty, verifier passed with zero
violations. Wall time and branch/conflict counts vary run to run (CP-SAT
parallel search is not deterministic in wall-clock terms even with a
fixed seed), but every run lands well under a second-and-a-half -- no
wild or intermittent behavior.

### Tight (2 runs, different seeds)

| Run | Status | Penalty | Wall time (s) | Branches | Conflicts | Verifier |
|---|---|---|---|---|---|---|
| 1 | OPTIMAL | 0 | 1.23 | 8,022 | 0 | passed |
| 2 | OPTIMAL | 0 | 0.89 | 16,693 | 54 | passed |

Both runs: OPTIMAL, zero penalty, verifier passed. The extra
unavailability and doubled fixed-placement count did not push the
scenario into FEASIBLE-only or timeout territory on this machine.

### Impossible

Preflight: **0 errors** (confirms the fixture passes basic reference and
occupancy validation, as intended -- resource-capacity feasibility is not
something preflight checks). CP-SAT: **INFEASIBLE** in 1.36s, 16,942
branches, 0 conflicts. No valid schedule was ever produced, matching the
required outcome exactly.

## CP-SAT model-size / scaling observations

From `SchedulingResult.metadata` on the standard scenario:

| Metric | Value |
|---|---|
| Lesson decision variables (`x[req, day, period]`) | 9,080 |
| Total CP-SAT variables (includes block/window/day-used auxiliary vars) | 9,493 |
| Total CP-SAT constraints | 5,300 |
| Requirements | 227 |
| Classes | 15 |
| Teachers | 34 |

Lesson variables dominate the variable count (9,080 of 9,493, ~96%) --
this is simply `num_requirements x num_days x num_periods` (227 x 5 x 8 =
9,080), i.e. it scales linearly in requirements and in the size of the
calendar, not combinatorially. The remaining ~400 variables are the
REQUIRED-pattern block/window booleans (only requirements with a REQUIRED
policy get these -- 11 requirements in this fixture) and the
`min_distinct_days` day-used booleans -- a small, bounded overhead per
requirement that actually needs them, not a global cost.

**No bottleneck was identified at this scale.** Zero-to-low conflict
counts (0-54 across 5 runs) and solve times under 1.5s indicate CP-SAT is
finding the feasible region almost immediately for this problem size --
the constraint structure (teacher/class non-overlap, occupancy, resource
capacity, generic REQUIRED blocks, split/merge sync) is not creating
meaningful search difficulty at 15 classes / 227 requirements / 600
class-slot units. This is consistent with the model growing linearly
(see above) rather than combinatorially with scale.

## Conclusion

The existing Phase 1/2A solver, preflight validator, and independent
verifier all held up cleanly at a genuinely realistic school scale with
no product-semantic changes and no HARD constraint weakened. The only
production changes made were the explicitly-scoped ones: `SolverOptions`
and richer benchmark metadata (see `DECISIONS.md`). See the Phase 2B
final report for the explicit go/no-go recommendation.
