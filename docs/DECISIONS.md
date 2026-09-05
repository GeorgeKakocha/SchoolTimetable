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

16. **A manual move is a validated swap, not a plain relocation
    (Phase 2C).** Full class occupancy is HARD and unconditional; a
    literal "move X from A to B" would leave A empty, which can never
    pass the same independent verifier every other schedule must pass.
    The only occupancy-preserving single-step edit is exchanging the
    moved occurrence with whatever fully occupies the target window for
    the same class(es). This resolves a genuine tension in the brief
    (which lists both "move one lesson" and "full class occupancy
    invalidated" as a rejection reason) rather than picking one
    arbitrarily; see `docs/SCHEDULE_EDITING.md` for the full reasoning.
    A swap is only attempted between occupants with the same class-set
    and the same block length -- anything else is rejected with a
    specific code, never attempted via a more complex cascade.

17. **A logical occurrence requires a period to identify, never just a
    day (Phase 2C).** Only REQUIRED and a formed PREFERRED double have a
    CP-SAT day-cap guaranteeing a requirement's periods on one day form a
    single block. FLEXIBLE has no such cap and, in the actual Phase-1
    fixture, routinely places several non-adjacent periods for the same
    requirement on the same day. `find_logical_occurrence`,
    `OccurrenceKey`, and the move/lock API were all designed around this:
    a day alone would silently over-glue independent FLEXIBLE
    occurrences into one move/lock unit.

18. **Re-optimization uses genuine two-phase lexicographic solving, not a
    weighted sum (Phase 2C).** Disruption from the reference schedule
    must strictly outrank the ordinary soft preferences. CP-SAT supports
    this cleanly: solve once minimizing disruption alone, pin the
    proven-optimal value with an equality constraint, solve again
    minimizing the ordinary soft objective. No numeric-weight compromise
    was needed, so none was made.

19. **`reoptimize()` reuses `model_builder`'s HARD-constraint functions
    directly (including its underscore-prefixed internals), rather than
    re-implementing them.** This guarantees re-optimization can never
    drift from what `solve()` enforces -- there is exactly one place each
    HARD rule is encoded. The trade-off (reaching across a module's
    "private" naming convention) was judged lower-risk than duplicating
    ~150 lines of constraint-building logic that must stay in lockstep.

20. **Phase 3: ports-and-adapters, locked in before any repository code
    exists.** The dependency direction is `api -> application ->
    repository ports (interfaces application/ defines) <- persistence
    (adapters implementing them)`; `application` depends only on those
    interfaces plus `domain`/`scheduling`, never on `persistence` or
    `sqlalchemy` directly, even transitively. This makes "persistence
    must not become the domain" a structural property (application
    literally cannot import an ORM model) rather than a convention to
    remember. Concrete repository `Protocol`s are deliberately **not**
    created in Phase 3A1 as empty scaffolding -- with no application use
    case yet, their real required methods aren't known, and guessing them
    now would be premature abstraction (Decision #12). They arrive in
    Phase 3A2 alongside the first real persistence use case.

21. **SQLAlchemy ORM models are separate classes from `domain/`'s
    dataclasses, mapped explicitly -- never the same classes.** Every
    `domain/` type is a frozen dataclass, and that immutability is
    load-bearing: Phase 2C's stale-`MovePlan` safety, `_plans_equivalent`,
    and `Schedule.with_entries`/`with_locked` all depend on domain objects
    never being mutated in place, which fights directly against a
    SQLAlchemy ORM instance's mutable, session-tracked identity. Making
    `TeachingRequirement` etc. also an ORM model would either break that
    immutability or force an awkward half-frozen shape, and would pull
    `sqlalchemy` straight into `domain/`, which must stay dependency-free
    (`run_poc.py`/`run_scale_benchmark.py`/`run_editing_demo.py`, and the
    entire `tests/` suite, must keep working with zero web/DB
    dependencies installed -- verified as part of every Phase 3 slice's
    validation, not just assumed).

22. **PostgreSQL is the only supported database, in every environment,
    including local development and the automated web/persistence test
    suite.** No SQLite stand-in, ever -- Postgres-specific behavior
    (constraint semantics, connection/session behavior) must be exercised
    by the same engine used in production from day one. Phase 3A1 ships a
    single `docker-compose.yml` Postgres 16 service with two databases
    (the app's own, plus a separate one for `tests_web/`, created by
    `docker/init-test-db.sh`) rather than two different engines.

23. **The web/persistence test suite (`tests_web/`) is a separate suite
    from `tests/`, not collected by a plain `pytest -q`.** `tests/`
    (the pure domain/solver suite) must stay exactly as fast and
    dependency-free as it already is -- it needs neither the `web` extras
    nor a database. `tests_web/` needs both, and skips (never fails)
    tests that require a live PostgreSQL when one isn't reachable, so it
    behaves correctly whether or not `docker compose up -d db` has been
    run.

24. **Credential policy.** Python/application configuration must never
    hard-code a `DATABASE_URL`, username, password, or other credential
    default -- `Settings.database_url` (`config.py`) has no default, and
    every environment, including local development, must set
    `DATABASE_URL` explicitly via `.env` (gitignored) or the environment.
    `.env.example` *may* (and does) contain clearly documented
    local-development placeholder credentials, since it is not itself
    read as configuration (it is a template a developer copies to
    `.env`) and is never committed with real values. `docker-compose.yml`
    *may* similarly provide clearly documented local-development fallback
    values for its Postgres service, provided they are
    environment-overridable (`${POSTGRES_USER:-school_timetable}` etc. --
    a real deployment can override every one without editing the compose
    file) and never presented as production credentials. The container's
    published port is loopback-only by default
    (`${POSTGRES_BIND_HOST:-127.0.0.1}`), so even the placeholder
    credentials are not reachable off the host by default. Real
    production credentials are never committed anywhere in this
    repository.

25. **`GET /health`'s 503 body is generic, never diagnostic.** On a
    database connectivity failure the response is exactly
    `{"status": "error", "database": "unreachable"}` -- the underlying
    `SQLAlchemyError` (which can embed the connection string, host, or
    driver-level detail) is caught and discarded, never interpolated
    into the response. `/health` is deliberately unauthenticated (that's
    the point of a health check), so its failure body must never leak
    anything an attacker could use; real diagnostics belong in server
    logs, not the HTTP response. Verified in this session with a live
    PostgreSQL: 200 `{"status": "ok", "database": "ok"}` when reachable,
    and 503 with the generic body (asserted free of the probe
    credentials/host used to trigger it) when not.

26. **Phase 3A2.1 locked persistence schema for the complete
    `SchedulingProblem` input surface** (`persistence/models.py`, one
    Alembic revision `8cdd513e16da` on top of the empty baseline
    `e2cbe4786a14`). No repository/mappers/API exist yet -- see #27.

    - **Scope**: one `academic_year_id` is the entire persisted
      configuration snapshot (School, AcademicYear, Day, Period,
      ClassSection, ParticipantGroup, Teacher, TeacherAvailability,
      Activity, Resource, TeachingRequirement, TimePreference,
      ReservedBlock, FixedPlacement, and their child/join tables -- 17
      tables total). No finer-grained mid-year configuration versioning
      exists or is planned for this phase; a school-wide
      personnel/resource directory reused *across* academic years is
      explicitly a separate, not-yet-decided product concern, not a
      silent change to this aggregate's scope.
    - **Identity**: every surrogate PK is `BIGINT GENERATED ALWAYS AS
      IDENTITY`, persistence-only, never exposed outside
      `persistence/`. Every domain string ID is stored verbatim in a
      `natural_id` column (`UNIQUE(natural_id)` for `school`;
      `UNIQUE(school_id, natural_id)` for `academic_year`;
      `UNIQUE(academic_year_id, natural_id)` for everything scoped
      under one academic year).
    - **Same-academic-year isolation, enforced by PostgreSQL, not just
      Python**: every cross-entity reference within one academic year
      is a composite FK, `FOREIGN KEY (academic_year_id, x_id)
      REFERENCES x (academic_year_id, id)`, requiring the target table
      to also carry `UNIQUE(academic_year_id, id)`. A row in one
      academic year can never reference a sibling entity belonging to
      a different academic year -- proven by
      `test_cross_academic_year_references_are_rejected` in
      `tests_web/test_persistence_schema.py` against a real
      PostgreSQL, not merely asserted.
    - **No JSONB anywhere.** `LessonBlockPolicy.mode` /
      `DistributionPolicy`'s two fields / `ResourceRequirement.resource_id`
      are typed scalar columns directly on `teaching_requirement`;
      `LessonBlockPolicy.block_sizes` and
      `TimePreference.preferred_periods` are `SMALLINT[]` (arrays
      preserve order natively, so need no extra ordinal column);
      `TimePreference` (genuine 0..N cardinality per requirement) is a
      normalized child table. `split_group_id` stays a bare
      synchronization label with no FK -- no `SplitGroup` entity exists,
      matching the domain's own choice.
    - **Enums are `TEXT + CHECK`**, never native PostgreSQL `ENUM`
      types, for every one of the four enum fields
      (`TeacherAvailability.status`, `Activity.kind`,
      `TeachingRequirement.block_mode`, `TimePreference.weight`).
    - **Exact tuple order is preserved on every persisted domain
      tuple.** `Day.idx`/`Period.idx` are the domain's own ordering
      fields and double as the order-preserving column for
      `SchedulingProblem.days`/`.periods`. Every other top-level
      collection that has no intrinsic domain ordering
      (`class_section`, `participant_group`, `teacher`,
      `teacher_availability`, `activity`, `resource`,
      `teaching_requirement`, `reserved_block`, `fixed_placement`)
      carries an explicit `ordinal SMALLINT NOT NULL` +
      `UNIQUE(academic_year_id, ordinal)`. Nested tuple collections
      (`ParticipantGroup.class_sections`, `TeachingRequirement.
      time_preferences`, `ReservedBlock.class_sections`,
      `ReservedBlock.slots`) use the same `ordinal` pattern scoped to
      their parent row (`PRIMARY KEY (parent_id, ordinal)`), plus a
      separate structural-duplicate-prevention `UNIQUE` constraint
      (e.g. `UNIQUE(participant_group_id, class_section_id)`) so the
      same child can never appear twice in one parent's tuple.
    - **Delete semantics are aggregate-oriented, not blanket CASCADE**:
      (A) every table's direct `academic_year_id` FK is `ON DELETE
      CASCADE` (deleting a whole configuration snapshot removes
      everything under it); (B) true owned-child edges are `CASCADE`
      (`participant_group -> participant_group_class_section`,
      `teacher -> teacher_availability`, `teaching_requirement ->
      time_preference`/`fixed_placement`, `reserved_block ->
      reserved_block_class_section`/`reserved_block_slot`); (C) every
      other cross-entity reference, including both optional ones
      (`teaching_requirement.resource_id`, `reserved_block.teacher_id`),
      is `ON DELETE RESTRICT` -- deleting a referenced teacher,
      activity, participant group, resource, class section, day, or
      period must never silently cascade away or null out an unrelated
      curriculum object; that stays an explicit future application
      operation, not implicit database mutation. Verified against a
      real PostgreSQL in `tests_web/test_persistence_schema.py`
      (owned-child CASCADE, cross-entity RESTRICT including the two
      optional edges never silently nulling).
    - **Migration discipline**: the ORM models and the Alembic
      migration were produced in the same slice
      (`alembic revision --autogenerate` against `Base.metadata`, then
      hand-reviewed) so they cannot drift apart from the start;
      confirmed via a second `--autogenerate` run against the
      migrated-to-head database producing an empty diff (no operations),
      and via upgrade/downgrade/upgrade round-tripped against the real
      PostgreSQL *test* database (never the development one, to keep
      destructive reversibility testing off the DB a developer actually
      uses locally).

27. **Phase 3A2.1 is schema-only.** No `application/` repository
    Protocol, no domain <-> persistence mapper, no `GET /config` API,
    and no seed/production data-loading path exist yet -- all deferred
    to Phase 3A2.2+, per the locked repository-port design (a single
    `SchedulingProblemRepository.load_by_school_and_year(...)`, no
    generic CRUD). `fixtures/` remains imported only by `tests/`,
    `tests_web/`, and the existing demo/benchmark scripts -- never by
    `persistence/`, `application/`, or `api/`.

28. **Phase 3A2.2 (`persistence/mappers.py`) implements only
    persistence -> domain mapping; domain -> persistence is
    intentionally absent from production code.** Domain objects
    reference each other solely by natural string ID, while ORM rows
    (Phase 3A2.1) reference each other by surrogate `BIGINT` FK --
    writing therefore needs whole-graph natural-id -> surrogate-id
    resolution across every entity in one academic year, a materially
    different, aggregate-aware concern from mapping a single
    already-loaded row back to its domain type. That direction is
    deferred to a TEST-ONLY aggregate writer, Phase 3A2.3 (living only
    under `tests_web/`, never imported or referenced by `persistence/`,
    `application/`, or `api/`); no `save`/`create`/`upsert`/
    `from_domain`/generic-CRUD function exists anywhere in
    `mappers.py`.

    Every mapper is a pure function of already-fetched ORM rows (plus,
    where a sibling FK needs resolving, a `NaturalIdLookup` --
    persistence-only, built once via `.build()` from whatever rows the
    caller already has, never queried itself) -- no `Session`, no
    query, no engine, no `fixtures/` import. An unresolved surrogate
    reference raises a clear `KeyError` immediately rather than
    silently returning the surrogate or inventing a natural ID.

    Every ORM-side tuple-order-preserving `ordinal` column from
    Decision #26 is honored explicitly in the mapper, not assumed from
    database return order: `participant_group_to_domain`,
    `teaching_requirement_to_domain` (for `time_preferences`), and
    `reserved_block_to_domain` (for `class_sections` and `slots`) each
    sort their child rows by `ordinal` before building the domain
    tuple, proven by unit tests that deliberately supply those child
    rows out of order. `TimePreference.preferred_periods` round-trips
    the stored `Period.index` integers verbatim -- never resolved
    through `NaturalIdLookup` as if they were `Period.id` references,
    preserving the domain's own index-based semantics exactly (see
    Decision #26's `TimePreference` note).

29. **Phase 3A2.3's `SchedulingProblemRepository` is the first real
    `application/` use case, and it stays exactly that -- one method,
    no generic CRUD.** `application/ports.py` defines
    `SchedulingProblemRepository` as a `typing.Protocol` with only
    `load_by_school_and_year(school_natural_id, academic_year_natural_id)
    -> SchedulingProblem`; no `save`/`create`/`update`/`delete`/
    `list_*`/`get_*` is added ahead of an actual use case that needs
    one (Decision #12). `application/` imports only `domain/` -- never
    SQLAlchemy, `persistence/`, FastAPI, or `fixtures/`, even
    transitively -- matching the locked ports-and-adapters direction
    from `docs/ARCHITECTURE.md`.

    Not-found is one concept, `application/errors.py`'s
    `SchedulingProblemNotFoundError`, carrying only the natural
    `school_natural_id`/`academic_year_natural_id` the caller supplied
    -- never a SQLAlchemy exception or a persistence surrogate ID.
    `SqlAlchemySchedulingProblemRepository` (`persistence/
    problem_repository.py`) raises it identically whether the school
    itself doesn't resolve or the school resolves but the requested
    academic year doesn't -- both are the same "this configuration
    does not exist" outcome to a caller, and Phase 3A2.4's `GET /config`
    can map either directly to one 404 without inspecting which case
    occurred.

    The adapter's loading strategy is deliberately explicit rather than
    relying on ORM `relationship()`/lazy-loading: resolve `School` then
    `AcademicYear` by natural ID, then one `SELECT ... WHERE
    academic_year_id = :id` per scoped table, group child rows by
    parent surrogate ID in plain Python, build one `NaturalIdLookup`,
    and call Phase 3A2.2's mappers -- multiple explicit queries, never
    a premature generic query-builder abstraction. Every top-level
    tuple is sorted before mapping exactly per Decision #26's ordering
    rule (`Day`/`Period` by `idx`, everything else by `ordinal`) --
    never assumed from database return order. The repository
    reimplements no preflight/solver/verifier reasoning: a
    structurally loadable but domain-invalid configuration is expected
    to load successfully and be rejected by `validation.preflight`
    afterward, exactly like any other `SchedulingProblem`.

    Proof that this is not a silently-narrowed reconstruction: a
    TEST-ONLY aggregate writer (`tests_web/support/problem_writer.py`
    -- domain -> persistence, identity-resolution-aware, never imported
    or referenced by `persistence/`, `application/`, or `api/`, and
    explicitly not a production repository "save" method, per the
    persistence -> domain-only boundary in Decision #28) writes the
    complete `build_valid_fixture()` graph in dependency order; the
    production repository reads it back; the result is asserted
    **deeply equal** to the original `SchedulingProblem` (exact tuple
    order included, not a partial comparison), passes
    `run_preflight` with no errors, `solve()`s to the same
    `SolverStatus`, and passes the independent `verify()` -- proven
    against real PostgreSQL in `tests_web/test_problem_repository.py`.

30. **Phase 3A2.4's `GET /schools/{school_id}/years/{year_id}/config` is
    the first domain/business endpoint, and it is deliberately
    read-only and configuration-only.** No `POST /generate`,
    `POST /solve`, or `GET /schedule` exists or is implied by it; no
    solver/preflight is invoked from the route (that proof already
    exists in Phase 3A2.3's repository round-trip test). Path
    parameters (`school_id`, `year_id`) are natural/domain IDs -- the
    same `School.id`/`AcademicYear.id` strings `SchedulingProblemRepository`
    already takes -- never a persistence surrogate ID.

    The public JSON contract (`api/schemas.py`'s `SchedulingConfigResponse`
    and its nested models) is **hand-designed, one Pydantic model per
    current domain concept**, built by one pure, explicit
    `api/serializer.py` function
    (`config_response_from_problem(problem) -> SchedulingConfigResponse`)
    -- never `dataclasses.asdict()`, never generic reflection, never an
    ORM row serialized directly. This is deliberate: a future domain
    field added to `SchedulingProblem` must never silently appear in
    the public API contract; someone has to decide to expose it, by
    adding it to both `schemas.py` and `serializer.py`. The serializer
    itself never re-sorts or re-groups anything -- exact tuple order is
    inherited verbatim from the domain tuples `SchedulingProblemRepository`
    already assembled correctly (Decision #29); enums serialize as
    their existing string values (`AvailabilityStatus`, `ActivityKind`,
    `BlockPolicyMode`, `PreferenceWeight` -- unchanged from `domain/`,
    never a new API-only enum). No ORM `ordinal` value is ever exposed
    -- it is persistence infrastructure, not a public concept.

    `api/dependencies.py` is the composition root: the one place
    importing both `application/` and `persistence/` together (per the
    locked ports-and-adapters direction). It wraps the existing,
    already-correct `persistence.db.get_session` FastAPI dependency
    (one `Session` per request, always closed after) to construct
    `SqlAlchemySchedulingProblemRepository` per request -- no global
    long-lived `Session`, no new session-lifecycle code. The route
    itself (`api/config_routes.py`) is typed against
    `application.ports.SchedulingProblemRepository` (the Protocol), not
    the concrete adapter class, so a future alternative adapter could
    be substituted without touching the route. No application service
    layer was introduced for this one pass-through use case (Decision
    #12) -- the route calls the repository port directly; a real
    service will be added only when a genuine use case needs one
    (e.g. combining `scheduling/` with persistence).

    `application.errors.SchedulingProblemNotFoundError` maps to a
    generic HTTP 404 (`{"detail": "Scheduling configuration not
    found"}`) identically whether the school or the academic year is
    the part that doesn't resolve (matching the not-found contract
    already locked in Decision #29) -- no other exception is caught in
    the route, so an unexpected DB/programming failure still surfaces
    as a 500, never silently reinterpreted as "not found."

    Proven against real PostgreSQL through a real FastAPI `TestClient`:
    the existing TEST-ONLY writer seeds `build_valid_fixture()`, the
    `get_session` dependency is overridden (the same
    `app.dependency_overrides` pattern `test_health.py` already
    established) so the route's repository sees the identical seeding
    Session/transaction rather than an independently-committed one, and
    the full JSON response is asserted equal to the same serializer's
    output built directly from the original in-memory
    `SchedulingProblem` -- a complete-contract proof, not scattered
    field checks. A recursive test walks the entire response
    confirming no `academic_year_id`/`ordinal` key and no non-string
    `id`/`*_id` value appears anywhere.

31. **Phase 3A3 ADR: schedule generation + immutable schedule-version
    persistence -- design locked, implementation not yet started.**
    Formalizes, for the first time as a numbered decision, versioning
    semantics that had previously existed only as direct product-owner
    instruction in conversation, not as an authoritative repository
    record -- this entry is now that record. No ORM model, migration,
    repository, service, or API endpoint exists yet; this is the locked
    design the Phase 3A3.1-3A3.4 implementation slices (see
    `docs/PROJECT_STATE.md`) must follow without redesign.

    **Owner Decision 1 -- one canonical `Schedule` per School +
    AcademicYear.** Each `academic_year_id` has exactly one `schedule`
    row (`UNIQUE(academic_year_id)`); history is represented entirely
    by immutable `schedule_version` rows hanging off it. Multiple
    independent scenarios/drafts are explicitly NOT in this MVP.
    Rationale: matches the stated Phase 3A3/3B goal exactly ("the"
    generated/persisted timetable, not "a" timetable among several);
    strictly simpler; does not foreclose a future scenario feature,
    which would relax `UNIQUE(academic_year_id)` to
    `UNIQUE(academic_year_id, natural_id)` in a later, explicit
    migration rather than requiring a redesign of anything already
    built on top of it.

    **Owner Decision 2 -- Generate is initial-generation-only.** If a
    `schedule` row already exists for the requested school/year,
    `POST .../schedule/generate` returns an application-level conflict
    mapped to HTTP 409 -- it never generates a fresh-from-scratch
    version, never appends a version, and never silently reoptimizes.
    Manual editing and reoptimization are a separate use case (Phase
    3A4, not yet scoped) with their own semantics for producing new
    versions; Generate must not be conflated with them, and doing so
    would require locks/a reference schedule this endpoint has no
    business assuming.

    **Owner Decision 3 -- Phase 3A3's read API is generic and flat.**
    `GET /schools/{school_id}/years/{year_id}/schedule/active` returns
    the active persisted `ScheduleVersion` plus a flat entry list, using
    only natural/domain IDs and the explicitly selected public fields
    this contract needs -- structurally the same kind of hand-designed,
    non-UI-shaped contract `/config` already is. Persisted entry
    *order* is exact and deterministic (see the `schedule_entry.ordinal`
    correction below); the API is free to present that ordered list as
    a flat array without imposing any further shape on it. No
    React-specific 5x8 class-timetable projection, and no UI-facing
    "ordered days -> periods -> cells" shaping, is implemented in Phase
    3A3 -- that projection contract belongs entirely to Phase 3B, once
    a real consumer exists to validate its shape against. Phase 3A3
    must not be described, designed, or implemented as if it already
    produces that projection.

    **Owner Decision 4 -- transaction boundary excludes the solve; no
    DB session/connection of any kind exists during preflight, solve,
    or verify.** The generation flow is three strictly separated phases:

    - **(A) Read.** A persistence-side operation opens its own short
      `Session`, loads the frozen `SchedulingProblem`, and closes that
      `Session` before returning control to the application layer.
      `GenerateScheduleService` never receives, holds, or is constructed
      with a `Session` -- it calls a port method and gets back a plain
      `SchedulingProblem`.
    - **(B) Pure application work.** `run_preflight`, `solve`, `verify`
      run entirely in-process, with **no DB `Session` or connection open
      anywhere for the duration** -- not held, not idle-in-transaction,
      not deferred-close. This is the actual guarantee Owner Decision 4
      protects: a 30-second CP-SAT solve must never correspond to an
      open database connection.
    - **(C) Write.** Only if verification passes, a *different*
      persistence-side operation opens a fresh short `Session`/write
      transaction, atomically persists `Schedule` + `ScheduleVersion` +
      entries + the active-version pointer (see the concurrency
      correction below), commits, and closes.

    No Unit-of-Work abstraction is introduced. `GenerateScheduleService`
    must never be bound to a request-scoped SQLAlchemy `Session` the way
    `api/dependencies.py` currently wires `get_session()` into
    `SqlAlchemySchedulingProblemRepository` for a plain read -- doing so
    for generation would keep FastAPI's yield-based request `Session`
    open for the entire request, including the solve, which is exactly
    what this decision forbids. Instead, the concrete
    `ScheduleVersionRepository` adapter's methods (`get_active_schedule`,
    `persist_initial_version`) each open and close their own short
    session internally (e.g. backed by a session *factory*, not a
    single injected `Session`) -- the API composition root may
    construct such a session-factory-backed adapter, but
    `GenerateScheduleService` itself remains unaware that SQLAlchemy (or
    any database) exists at all, consistent with `application/`'s
    existing no-SQLAlchemy boundary. Phase 3A3.3/3A3.4 must implement
    this literally -- not "reuse the request `Session` for convenience"
    -- since that would silently reintroduce the held-connection
    problem this decision exists to prevent.

    **Owner Decision 5 -- persisted solver/audit metadata.**
    `schedule_version` persists `solver_status`, `total_soft_penalty`,
    `wall_time_seconds`, `random_seed` (nullable), and `created_at`.
    It does **not** persist `num_conflicts`, `num_branches`,
    `num_cp_variables`, `num_cp_constraints`, `best_objective_bound`, or
    any other CP-SAT implementation telemetry -- these are solver
    internals with no product meaning and would couple the schema to
    today's specific solver instrumentation. A verifier failure is
    never persisted as a successful `ScheduleVersion` at all; the mere
    existence of a persisted version *is* the proof verification
    passed, so no separate "verifier result" column is needed.

    **Versioning rules formalized** (previously conversation-only
    product direction; now authoritative here):
    - `ScheduleVersion` is immutable: no repository method ever
      updates or deletes one once created.
    - `Schedule` has an explicit `active_version_id`.
    - `ScheduleVersion` has `version_number` (unique within its
      `Schedule`) and `parent_version_id` (nullable, same-`Schedule`
      lineage).
    - A successful initial Generate creates `Schedule` + version
      `version_number = 1, parent_version_id = NULL`, and makes that
      version active, atomically.
    - Existing versions are never mutated in place, ever, by any future
      phase.
    - Every future accepted manual edit (Phase 3A4) creates exactly one
      new `ScheduleVersion`, referencing its parent; reoptimization
      likewise creates a new version rather than mutating an old one,
      preserving the existing disruption/lock semantics
      (`scheduling/reoptimize.py`, unchanged) rather than behaving like
      a from-scratch Generate.
    - Locks belong to a specific persisted version and logical
      occurrence (`locked_occurrence`, keyed exactly like the existing
      in-memory `OccurrenceKey`), never to the global
      `TeachingRequirement` -- unchanged from the already-implemented
      Phase 2C semantics (Decision #16-19).

    **Schema** (proposed now, implemented in Phase 3A3.1 -- not yet
    created):

    `schedule`: `id BIGINT IDENTITY` PK; `academic_year_id BIGINT NOT
    NULL REFERENCES academic_year(id) ON DELETE CASCADE`;
    `UNIQUE(academic_year_id)` (Decision 1), explicitly named
    `uq_schedule_academic_year_id` (this exact name is required -- see
    "Concurrent double-Generate race" below, which matches violations of
    it by name); `UNIQUE(academic_year_id, id)` (composite-FK target for
    `schedule_version`);
    `active_version_id BIGINT NULL` (nullable only for the brief window
    between creating `schedule` and creating its first version -- see
    the active-version FK design below). **No public natural schedule
    ID is introduced.** The canonical schedule is fully identified by
    `(school_id, academic_year_id)` alone in every public API -- the
    same two natural IDs `/config` already uses -- so a `natural_id`
    column on `schedule` would have no caller that needs it; one is
    added only if a concrete future requirement proves otherwise.

    `schedule_version`: `id BIGINT IDENTITY` PK; `academic_year_id
    BIGINT NOT NULL REFERENCES academic_year(id) ON DELETE CASCADE`
    (denormalized down, matching every other child-of-child table in
    this schema); `schedule_id BIGINT NOT NULL`, composite FK
    `(academic_year_id, schedule_id) REFERENCES schedule(academic_year_id,
    id) ON DELETE CASCADE` (a version has no meaning without its
    schedule -- true owned child); `version_number INTEGER NOT NULL`,
    `UNIQUE(schedule_id, version_number)`; `parent_version_id BIGINT
    NULL`, composite FK `(schedule_id, parent_version_id) REFERENCES
    schedule_version(schedule_id, id) ON DELETE NO ACTION` (nullable for
    the root version; a parent is a same-`Schedule` sibling -- see "Delete
    action for the lineage/active-version references" below for why
    this is `NO ACTION`, not `RESTRICT`);
    `UNIQUE(schedule_id, id)` (composite-FK target for `schedule.
    active_version_id`, see below); `solver_status TEXT NOT NULL CHECK
    (solver_status IN ('OPTIMAL','FEASIBLE'))` (only these two ever
    reach persistence -- `INFEASIBLE`/`INVALID_INPUT`/`ERROR` are never
    persisted at all, per the transaction-boundary flow);
    `total_soft_penalty INTEGER NOT NULL`; `wall_time_seconds DOUBLE
    PRECISION NOT NULL`; `random_seed INTEGER NULL`; `created_at
    TIMESTAMPTZ NOT NULL DEFAULT now()`. **No public natural version
    ID is introduced** -- `(school_id, academic_year_id, version_number)`
    is sufficient to address any version publicly; `version_number`
    itself is the stable, natural, human-meaningful handle.

    `schedule_entry`: `id BIGINT IDENTITY` PK (no natural ID -- this row
    has no domain-facing identity of its own, matching
    `teacher_availability`/`time_preference`'s existing no-natural-ID
    pattern); `academic_year_id BIGINT NOT NULL REFERENCES
    academic_year(id) ON DELETE CASCADE`; `schedule_version_id BIGINT
    NOT NULL`, composite FK `(academic_year_id, schedule_version_id)
    REFERENCES schedule_version(academic_year_id, id) ON DELETE
    CASCADE` (true owned child); `source TEXT NOT NULL CHECK (source IN
    ('REQUIREMENT','RESERVED_BLOCK'))`; `day_id BIGINT NOT NULL`,
    composite FK to `day(academic_year_id, id) ON DELETE RESTRICT`;
    `period_id BIGINT NOT NULL`, composite FK to
    `period(academic_year_id, id) ON DELETE RESTRICT`;
    `teaching_requirement_id BIGINT NULL`, composite FK to
    `teaching_requirement(academic_year_id, id) ON DELETE RESTRICT`;
    `reserved_block_id BIGINT NULL`, composite FK to
    `reserved_block(academic_year_id, id) ON DELETE RESTRICT`; `CHECK
    ((source = 'REQUIREMENT') = (teaching_requirement_id IS NOT NULL)
    AND (source = 'RESERVED_BLOCK') = (reserved_block_id IS NOT NULL))`
    so exactly one of the two FK columns is populated per row, matching
    `source`; **`ordinal SMALLINT NOT NULL`**, `UNIQUE(schedule_version_id,
    ordinal)`. `Schedule.entries` is a `tuple[ScheduleEntry, ...]`, not
    a set -- SQL row order is never guaranteed, so exact round-trip
    reconstruction (Phase 3A3.2's own acceptance criterion) requires an
    explicit ordinal, exactly the same tuple/list-needs-an-ordinal rule
    already locked for every Phase 3A2.1 table (Decision #26). The
    persistence writer assigns `ordinal` from `enumerate(schedule.entries)`;
    the read path reconstructs the tuple `ORDER BY ordinal`, never by
    unordered `SELECT` result order. **Deliberately not stored**: `activity_id`, `teacher_id`,
    `participant_group_id`, `resource_id`, `class_sections` -- every one
    of these is fully re-derivable at read time by joining back to the
    referenced `teaching_requirement`/`reserved_block`, exactly as
    `api/serializer.py` already resolves natural IDs for `/config`; no
    concrete Phase 3A3 correctness requirement forces denormalizing
    them, and doing so would duplicate configuration data across every
    version indefinitely for no benefit. **Known, accepted limitation**:
    because Phase 3A2 has no configuration versioning (one persisted
    `SchedulingProblem` snapshot per `academic_year_id`, mutable only by
    a not-yet-existing future write path), a historical
    `ScheduleVersion` reloaded later will show the *current*
    configuration's teacher/activity/policy values for its
    `teaching_requirement_id`/`reserved_block_id` joins, not necessarily
    the values that were true when that version was generated. This is
    accepted for the current MVP specifically because production
    configuration editing does not exist yet -- "current config" and
    "config at generation time" are provably identical by construction
    until a config-write path is introduced. Solving this (e.g. by
    snapshotting configuration into each version) is out of scope for
    Phase 3A3 and must not be invented speculatively now.

    `locked_occurrence`: no surrogate PK -- the natural composite key
    mirrors the domain's own `OccurrenceKey` exactly (same pattern as
    `teacher_availability`'s no-surrogate-PK design). `academic_year_id
    BIGINT NOT NULL REFERENCES academic_year(id) ON DELETE CASCADE`;
    `schedule_version_id BIGINT NOT NULL`, composite FK
    `(academic_year_id, schedule_version_id) REFERENCES
    schedule_version(academic_year_id, id) ON DELETE CASCADE`;
    `teaching_requirement_id BIGINT NOT NULL`, composite FK to
    `teaching_requirement(academic_year_id, id) ON DELETE RESTRICT`;
    `day_id BIGINT NOT NULL`, composite FK to `day(academic_year_id,
    id) ON DELETE RESTRICT`; `anchor_period_id BIGINT NOT NULL`,
    composite FK to `period(academic_year_id, id) ON DELETE RESTRICT`;
    `PRIMARY KEY (schedule_version_id, teaching_requirement_id, day_id,
    anchor_period_id)`. Deliberately **no `ordinal` column**: unlike
    `schedule_entry.entries` (a `tuple`, needing one), the in-memory
    type here is `Schedule.locked_occurrences: frozenset[OccurrenceKey]`
    -- a genuine set with no order to preserve, so adding an ordinal
    would be exactly the "column the domain type doesn't need" this
    schema otherwise avoids. A freshly generated version 1 normally has zero rows here; the
    table exists now purely so Phase 3A4's persisted editing/
    reoptimization semantics (which need locks scoped to a specific
    persisted version) do not force a schema redesign later.

    **Active-version circular FK.** `schedule.active_version_id` is
    guaranteed to belong to that same `schedule` row by a composite FK:
    `FOREIGN KEY (id, active_version_id) REFERENCES
    schedule_version(schedule_id, id)` (requiring `schedule_version`'s
    `UNIQUE(schedule_id, id)` above as its target). Creation sequence,
    exactly: (1) insert `schedule` with `active_version_id NULL` (the
    column is nullable for exactly this reason); (2) insert the
    `schedule_version` row for version 1 (`schedule_id` = that
    schedule's `id`); (3) `UPDATE schedule SET active_version_id =
    <version 1's id>`. At the moment step (3) executes, the referenced
    `schedule_version` row from step (2) already exists as a committed
    (or at least already-inserted, same-transaction) row -- so ordinary
    immediate, non-deferred FK enforcement is sufficient for this
    specific three-step sequence, and no `DEFERRABLE` constraint is
    needed. This is a narrow claim about *this* sequence only, not a
    general statement about when PostgreSQL checks constraints in
    other contexts. Delete action on this FK is `NO ACTION` -- see the
    dedicated subsection immediately below for why, and how that
    differs from the ordinary cross-entity `RESTRICT` rule this schema
    otherwise uses.

    **Delete action for the lineage/active-version references.**
    `schedule.active_version_id -> schedule_version` and
    `schedule_version.parent_version_id -> schedule_version` are not
    ordinary cross-entity references (Decision #26's category C,
    `RESTRICT`) -- they participate in the version graph's own
    self-lineage/back-reference cycle, while the owning
    `academic_year -> schedule -> schedule_version -> schedule_entry`/
    `locked_occurrence` chain must still support whole-snapshot root
    deletion via `CASCADE` (Decision #26's category A/B, unchanged and
    still `CASCADE` throughout). Using plain cross-entity `RESTRICT` on
    the two lineage edges would work for a *direct*, isolated delete of
    one `schedule_version` row, but would needlessly complicate --  or
    on some engines outright block -- the *root* cascade that must still
    be able to remove the entire version graph in one `academic_year`
    (or, transitively, `schedule`) deletion, since `RESTRICT` and
    `CASCADE` interacting on the same target row across different FK
    paths is exactly the kind of interaction this schema otherwise
    avoids by keeping ownership edges and lineage edges on genuinely
    different delete actions.

    Locked behavior: both `schedule.active_version_id -> schedule_version`
    and `schedule_version.parent_version_id -> schedule_version` use
    PostgreSQL's ordinary default, `ON DELETE NO ACTION` (i.e. no
    explicit `ON DELETE` clause at all) -- not `RESTRICT`, not
    `SET NULL`, not `DEFERRABLE`, no trigger. Concretely:
    - A direct `DELETE FROM schedule_version WHERE id = <active
      version>` fails: `schedule.active_version_id` still points at it
      when that statement's constraints are checked, and `NO ACTION`
      rejects exactly like `RESTRICT` would in this isolated case.
    - A direct `DELETE FROM schedule_version WHERE id = <a parent still
      referenced by a child's parent_version_id>` likewise fails, for
      the same reason.
    - A `DELETE FROM academic_year WHERE id = <this year>` (or,
      transitively, a `schedule` row's own `CASCADE` chain) removes
      every `schedule_version` row for that year together, in the same
      statement, via the owning `CASCADE` edges -- by the time `NO
      ACTION`'s check would otherwise fire, the referencing
      `active_version_id`/`parent_version_id` values have already been
      removed along with everything else in that one cascade, so
      nothing is left to violate the constraint.
    - No `SET NULL` is used (an active-version pointer or a lineage
      link silently going `NULL` would misrepresent history); no
      `DEFERRABLE` is used (not needed for either the creation sequence
      above or the deletion behavior here); no trigger is used (Decision
      #21's existing preference for application-level, not
      database-mechanism, enforcement, reinforced by Decision #31's own
      immutability section below).
    - This is pure defense-in-depth, not a user-facing delete feature:
      no repository method ever deletes a `schedule_version` directly
      in Phase 3A3 (see the immutability section below); the only
      delete path that must keep working is the existing whole-snapshot
      `academic_year` root cascade Phase 3A2.1 already proved.

    **Required Phase 3A3.1 live-PostgreSQL tests** (to be written when
    that slice is implemented, not now): (1) deleting the current
    active `schedule_version` directly is rejected; (2) deleting a
    `schedule_version` that is still referenced as another version's
    `parent_version_id` is rejected; (3) deleting the owning
    `academic_year` root successfully cascades the entire `schedule` /
    `schedule_version` / `schedule_entry` / `locked_occurrence` graph in
    one statement, mirroring the existing Phase 3A2.1 whole-snapshot
    delete test. If real PostgreSQL behavior during 3A3.1 implementation
    contradicts this design (e.g. the root cascade does not in fact
    clear the lineage/active-version references before `NO ACTION`
    would check them), implementation must **stop and report** the
    contradiction rather than silently altering these locked semantics.

    **Concurrent double-Generate race.** Owner Decision 2 (Generate
    only when no canonical `Schedule` exists) has an inherent
    check-then-act race: two concurrent requests can each observe "no
    `Schedule` exists" via `get_active_schedule` and both proceed
    through preflight/solve/verify to `persist_initial_version`. The
    database's `schedule.UNIQUE(academic_year_id)` constraint --
    explicitly named **`uq_schedule_academic_year_id`**, following the
    same deterministic `uq_<table>_<columns>` naming convention every
    other constraint in `persistence/models.py` already uses (e.g.
    `uq_academic_year_school_natural_id`, `uq_day_ay_natural_id`) -- is
    the actual final concurrency guard, not the earlier application-level
    check, which is only an optimization to fail fast in the common
    case. Locked behavior: `persist_initial_version` is atomic -- it
    creates `schedule` + `schedule_version` 1 + its `schedule_entry`
    rows + the `active_version_id` pointer inside one single write
    transaction (the three-step sequence above), never partially. If
    that transaction's `INSERT` into `schedule` loses the race, the
    persistence adapter must catch **specifically** a violation of the
    `uq_schedule_academic_year_id` constraint by name -- never a bare
    "any `IntegrityError`" -- and translate only that exact violation
    into the same `ScheduleAlreadyExists` application-level outcome
    Owner Decision 2 already defines for the ordinary pre-check case;
    a raw SQLAlchemy `IntegrityError` must never propagate to the API
    layer, and this expected, normal race must never surface as `500`.
    The losing transaction's partial rows (whatever was written before
    the conflicting `INSERT`) roll back automatically as part of that
    transaction failing -- no orphaned `schedule_version`/`schedule_entry`
    rows survive a lost race. Any *other* integrity violation caught
    during this write (one not matching `uq_schedule_academic_year_id`
    by name -- an unrelated FK/CHECK/UNIQUE failure) is a genuine defect
    and must propagate as
    one, never silently reinterpreted as `ScheduleAlreadyExists`. No
    distributed lock, advisory lock, or job queue is introduced for
    this in Phase 3A3 -- the unique constraint plus one atomic
    transaction plus this one narrow exception translation is the
    complete MVP concurrency strategy.

    **Application architecture.** `application/`'s first genuine
    orchestration service, `GenerateScheduleService` (not a generic
    `ScheduleService`), depends only on: the existing
    `application.ports.SchedulingProblemRepository`;
    `validation.preflight.run_preflight`; `scheduling.solver.solve`;
    `verification.verifier.verify`; and one new application-owned port,
    below. It must not depend on SQLAlchemy, ORM models, concrete
    persistence adapters, or FastAPI -- identical boundary discipline to
    every prior `application/` rule (Decision #27, #29).

    **`SchedulingProblemRepository` session-ownership clarification for
    generation (no new port introduced).** `application/` continues to
    own and use the existing `application.ports.SchedulingProblemRepository`
    Protocol unchanged -- differing session-ownership semantics between
    call sites is not, by itself, a reason to duplicate an application
    port; the Protocol's contract (`load_by_school_and_year(...) ->
    SchedulingProblem`) is already session-agnostic by design. What must
    change is which *concrete* implementation of that Protocol Phase
    3A3's generation composition wires in. The existing concrete
    adapter, `persistence.problem_repository.SqlAlchemySchedulingProblemRepository`,
    is **session-bound**: it is constructed with an already-open
    `Session` (exactly how `api/dependencies.py`'s
    `get_scheduling_problem_repository` wires it today, from the
    request-scoped `get_session()` FastAPI dependency, for the simple
    `GET /config` read). That construction pattern is correct and may
    remain exactly as-is for the `/config` read path -- it is not
    redesigned by this ADR. It **must not** be reused as-is for
    generation: injecting a request-scoped, already-open `Session`
    into `GenerateScheduleService`'s repository call would keep that
    `Session` open for however long the subsequent CP-SAT solve takes,
    directly violating Owner Decision 4.

    Instead, Phase 3A3's generation composition must use a concrete
    implementation of the *same* `SchedulingProblemRepository` Protocol
    whose `load_by_school_and_year(...)` call **owns its own short
    `Session` internally**, backed by a session factory rather than an
    injected instance: it opens a `Session`, loads and fully detaches
    the frozen `SchedulingProblem`, closes that `Session`, and only then
    returns the plain domain object -- exactly mirroring the read
    half of Owner Decision 4's three-phase flow. `GenerateScheduleService`
    calls this exactly as it would call any other port implementation;
    it receives a `SchedulingProblem`, never a `Session`, and only
    *after* that call returns (and the `Session` behind it has already
    closed) does preflight/solve/verify begin. The exact class/file
    name for this session-owning implementation is a Phase 3A3.2/3A3.3
    implementation detail, not decided here -- the one invariant locked
    now is: **`GenerateScheduleService` receives repository *ports*,
    never SQLAlchemy `Session`s, and both the configuration-read
    operation and the schedule-write operation it depends on must each
    own their own short-lived session internally**, never a session
    handed to them from outside that could outlive their own single
    operation.

    **Minimal new application-owned port**, `ScheduleVersionRepository`
    (named for the operation it actually performs, not a generic
    `ScheduleRepository`):
    ```python
    class ScheduleVersionRepository(Protocol):
        def get_active_schedule(
            self, school_natural_id: str, academic_year_natural_id: str,
        ) -> ActiveScheduleVersion | None:
            """None if no Schedule exists yet for this school/year."""
            ...

        def persist_initial_version(
            self,
            school_natural_id: str,
            academic_year_natural_id: str,
            entries: tuple[ScheduleEntry, ...],
            solver_status: SolverStatus,
            total_soft_penalty: int,
            wall_time_seconds: float,
            random_seed: int | None,
        ) -> ActiveScheduleVersion:
            """Creates Schedule + its first ScheduleVersion (version_number=1,
            parent_version_id=None) and marks it active, atomically."""
            ...
    ```
    `ActiveScheduleVersion` is a plain application-owned dataclass
    (`version_number: int`, `solver_status: SolverStatus`,
    `total_soft_penalty: int`, `wall_time_seconds: float`,
    `random_seed: int | None`, `created_at: datetime`,
    `entries: tuple[ScheduleEntry, ...]`, `locked_occurrences:
    frozenset[OccurrenceKey]`) -- never an ORM row, never Pydantic. No
    `delete`, `update_version`, `list_all_versions`, scenario CRUD, or
    append/reoptimize method is added -- none is needed until Phase 3A4
    actually scopes the use case that would need it (Decision #12).

    **Generation outcome model.** `GenerateScheduleService` returns one
    of a small closed set of application-level outcomes, kept distinct
    from internal defects:
    - `ConfigNotFound` (school/year config does not resolve --
      `SchedulingProblemNotFoundError` from the existing repository)
    - `ScheduleAlreadyExists` (Owner Decision 2's conflict, including
      the losing side of the concurrent double-Generate race above)
    - `InvalidConfiguration` (preflight returned errors; carries the
      `ValidationError`s, safe to surface -- they describe the school's
      own configuration problems, never internals)
    - `Infeasible` (CP-SAT proved no solution exists under the
      configuration's current HARD constraints -- an expected, real
      outcome, not a defect; distinct from `InvalidConfiguration`: the
      configuration itself is valid, it simply admits no feasible
      timetable)
    - `SolverError` (internal solver/model-builder failure -- a defect;
      never expose the raw exception string through any future API)
    - `VerifierFailed` (solver claimed success but the independent
      verifier disagreed -- an internal defect per Decision #11's
      existing rule, never persisted, never returned as a valid
      schedule)
    - `Generated(ActiveScheduleVersion)` (success)

    **HTTP contract** (to be implemented in Phase 3A3.4, not now):
    `POST /schools/{school_id}/years/{year_id}/schedule/generate` --
    success response carries only public/domain concepts, never a
    surrogate `schedule_version` ID: `{"version_number": 1,
    "solver_status": "OPTIMAL", "total_soft_penalty": 0, "created_at":
    "...", "is_active": true}`. No raw entry list in this response --
    that is the read endpoint's job. `GET /schools/{school_id}/years/
    {year_id}/schedule/active` returns the active version's metadata
    plus a flat entry list using only natural/domain IDs, ordered
    exactly by the persisted `schedule_entry.ordinal` (that column
    itself is never exposed in the response body -- it is
    persistence-only ordering infrastructure, not a public field, same
    rule as every other `ordinal` in this schema per Decision #26).

    Each entry in that list carries, at minimum: `source` (`"REQUIREMENT"`
    or `"RESERVED_BLOCK"`), `day_id`, `period_id`, `requirement_id: str |
    None`, and `reserved_block_id: str | None` -- exactly one of the
    latter two populated, consistent with `source`
    (`REQUIREMENT` -> `requirement_id` set, `reserved_block_id` `None`;
    `RESERVED_BLOCK` -> the reverse). These two fields are **not**
    denormalized display convenience -- they are the persisted entry's
    actual domain source identity, exactly mirroring the
    `schedule_entry.teaching_requirement_id`/`reserved_block_id`
    columns, and must not be dropped from the generic read contract:
    different `TeachingRequirement` rows sharing the same derived
    `activity_id`/`teacher_id` would otherwise be indistinguishable
    from the response alone, and future logical-occurrence/manual-edit
    semantics (Phase 3A4) are `requirement_id`-keyed, not
    activity/teacher-keyed. The API additionally exposes the derived
    natural/domain fields already established (`activity_id`,
    `teacher_id`, `participant_group_id`, `resource_id`,
    `class_sections`), resolved via the same join-and-serialize
    discipline `/config` already established -- hand-designed Pydantic
    response models, never `dataclasses.asdict()`, never an ORM row, no
    `ordinal`/surrogate field anywhere, consistent with Decision #30.
    This is a generic flat data contract, not a UI-shaped one -- it
    does not group entries by day/period/class, and it is not Phase
    3B's "ordered days -> periods -> cells" projection.

    **HTTP failure mapping** (fully locked -- zero remaining owner
    decisions): `ConfigNotFound` -> **404**, reusing the exact existing
    `/config` not-found contract *unchanged* -- `{"detail": "Scheduling
    configuration not found"}`, no `code` field. This ADR does **not**
    redesign that Phase 3A2.4 contract; the generation endpoint returns
    the identical body for the identical underlying
    `SchedulingProblemNotFoundError`, precisely because nothing about
    "the configuration doesn't exist" changes meaning between reading
    it and generating from it.

    The three outcomes genuinely specific to generation each carry a
    stable, distinct `code` string precisely because they are new
    expected-failure/conflict shapes with no existing contract to
    reuse, and two of them share an HTTP status: `InvalidConfiguration`
    -> **422**, body including `{"code": "INVALID_CONFIGURATION", ...}`
    plus the safe `ValidationError` codes/messages -- means the
    configuration itself failed preflight/semantic validation.
    `Infeasible` -> **409**, body `{"code": "SCHEDULE_INFEASIBLE", ...}`
    -- means the configuration is valid but its current HARD constraints
    admit no feasible timetable. `ScheduleAlreadyExists` -> **409**,
    body `{"code": "SCHEDULE_ALREADY_EXISTS", ...}`. `ScheduleAlreadyExists`
    and `Infeasible` deliberately **share HTTP 409** but are different
    outcomes with different causes and different `code` values --
    clients must branch on `code`, not status code alone, to tell these
    two apart. This `code` requirement is scoped to exactly these three
    generation-specific outcomes, not a claim that every 4xx response
    anywhere in the system already carries one (`ConfigNotFound`'s
    reused 404 above is the explicit counterexample); unifying the
    API's error envelope more broadly, if ever wanted, is separate
    future work, not introduced by this ADR. `SolverError` -> 500,
    generic body only, no `code` needed (not a client-actionable
    outcome). `VerifierFailed` -> 500, generic body only, logged loudly
    server-side with full violation detail, never returned to the
    caller. No response in any of these paths ever includes SQL text,
    stack traces, surrogate IDs, or CP-SAT-internal fields. The exact
    JSON envelope beyond "a stable `code` string plus whatever safe
    diagnostic fields each of the three generation-specific outcomes
    needs" is a Phase 3A3.4 implementation detail, not fixed further
    here.

    **Immutability enforcement, MVP level**: frozen domain objects
    (`ScheduleVersion`... already frozen at the domain level once
    introduced) plus **no `update`/`delete` method anywhere in
    `ScheduleVersionRepository` or its adapter** plus the relational
    FK/uniqueness constraints above. No PostgreSQL trigger is added in
    Phase 3A3 -- triggers would be real belt-and-suspenders but are
    unjustified machinery at the current single-application-writer
    scope (no second write path, no raw SQL console, no external
    service touches these tables), matching this codebase's existing
    preference for application-level immutability guarantees over
    database-mechanism ones (Decision #21).
