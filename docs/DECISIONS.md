# Decisions

Architectural decisions accepted for this milestone. Product/architecture
authority for these rests with the project's product/architecture lead;
this implementation followed them as given.

1. **Long-term stack** is Python/FastAPI/SQLAlchemy 2.x/Alembic/PostgreSQL
   (backend), OR-Tools CP-SAT (solver), React/TypeScript/Vite (frontend),
   as a modular monolith with the solver isolated from API/UI.

2. **This milestone is solver-only.** No FastAPI, database, SQLAlchemy,
   Alembic, PostgreSQL, React, TypeScript, auth, user accounts, Docker,
   deployment, or REST endpoints are implemented yet.

3. **`ClassSection` and `ParticipantGroup` are distinct concepts.** A
   class is an administrative unit ("8-A"); a participant group is who
   actually attends a lesson (a whole class, a split branch, or a merged
   union of classes).

4. **The solver never chooses who teaches what.** That mapping
   (teacher -> group -> subject -> weekly periods) is a
   `TeachingRequirement` supplied by the school. The solver only decides
   *when* each required lesson occurs.

5. **Availability has three states**: `AVAILABLE`, `PREFER_NOT` (soft),
   `UNAVAILABLE` (hard).

6. **Lesson block policy has three modes**: `REQUIRED` (hard pattern),
   `PREFERRED` (soft pattern, penalized if broken), `FLEXIBLE` (no shape
   constraint).

   As of Phase 2A, `REQUIRED` supports an arbitrary multiset of positive
   block lengths summing to `weekly_periods` (e.g. `(2, 1, 1, 1)`,
   `(2, 2)`, `(3, 1)`), enforced exactly by CP-SAT -- not limited to a
   single double lesson. Preflight rejects a pattern that cannot possibly
   be placed (too many blocks for the configured days, a block exceeding
   `max_periods_per_day`, or a block longer than any available
   consecutive same-`block_id` run) before CP-SAT ever runs.

   `PREFERRED` is **intentionally not generalized** in Phase 2A: it keeps
   the original Phase-1 shape (at most one size-2 block, the rest
   singles), still enforced by `UNSUPPORTED_BLOCK_SIZE`. Reason: REQUIRED's
   generic encoding is a hard multi-block placement (a day either fully
   realizes a chosen length or is empty); generalizing PREFERRED the same
   way would mean the solver deciding, as a *soft* choice, which subset of
   an arbitrary block multiset to attempt to form -- a materially larger
   soft-optimization design than "encourage one specific double lesson
   into existence". That redesign was explicitly out of scope for this
   slice and is deferred, not silently reinterpreted.

7. **Distribution policy**: `max_periods_per_day` is HARD;
   `min_distinct_days` is SOFT. Both live on the individual
   `TeachingRequirement`, never as a global default.

8. **Split groups** (e.g. German/Russian) are modeled as two or more
   `TeachingRequirement`s sharing a `split_group_id`; the solver forces
   them into lock-step (identical slots every week) and treats them as a
   single occupancy unit for their shared parent class(es).

9. **Merged groups** (e.g. two classes combined for one lesson) are
   modeled as a single `TeachingRequirement` whose `ParticipantGroup`
   spans multiple `ClassSection`s; no extra solver machinery is needed
   beyond crediting occupancy to every class in the group.

10. **Club/reserved blocks are pre-determined, not solved.** A
    `ReservedBlock` fixes specific classes to specific slots outside of
    CP-SAT's decision space; ordinary lessons are excluded from those
    (class, slot) pairs at the variable-domain level.

11. **Independent verification is mandatory.** A CP-SAT claim of
    FEASIBLE/OPTIMAL is never trusted on its own; `verification/verifier.py`
    independently re-derives every hard constraint from the final
    `ScheduleEntry` list.

12. **No generic rule DSL, no AI, no premature abstraction.** Preflight,
    solver, and verifier are explicit Python, not a rule-scripting engine.

13. **`SolverOptions` (Phase 2B)** is a plain, OR-Tools-free dataclass in
    `scheduling/options.py` (`max_time_seconds`, `num_search_workers`,
    `random_seed`) controlling how CP-SAT runs. `solve(problem)` without
    it reproduces the exact prior default behavior; this is additive, not
    a breaking change.

14. **A realistic scheduling problem may return `FEASIBLE` rather than
    `OPTIMAL`** within its time budget, and that is a successful, valid
    result -- not a failure -- as long as the independent verifier
    passes. `UNKNOWN` (no solution found before the time limit) is the
    actual failure condition to investigate. Never weaken a SOFT or HARD
    constraint merely to force `OPTIMAL` status.

15. **Known-feasible fixture generation uses CP-SAT itself, not a
    hand-rolled constructor.** For the Phase 2B school-scale fixture, a
    zero-slack (every class exactly fully occupied), ~230-requirement
    problem is exactly the kind of densely-packed combinatorial structure
    where a single-pass greedy constructor can genuinely dead-end on an
    early commitment even though a valid global arrangement exists (this
    was attempted and hit that wall three times with three different
    targeted fixes -- see `docs/SCALE_VALIDATION.md`). The generator
    instead builds the problem with zero fixed placements, solves it once
    with the same validated `model_builder`/CP-SAT pipeline to obtain a
    witness, and promotes a small, explicit subset of that witness to
    real `FixedPlacement`s before discarding the rest. The benchmark that
    is actually measured afterwards calls `solve()` completely fresh (a
    separate `CpSolver` instance, no shared state) against the resulting
    problem -- so no placement decision beyond the deliberately chosen
    fixed lessons ever crosses from fixture generation into the
    benchmark under test.
