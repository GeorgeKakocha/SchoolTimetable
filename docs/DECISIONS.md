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

    **Phase 3A3.4 final HTTP contract details -- owner decisions A/B/C,
    now locked; zero remaining.** A Phase 3A3.4 pre-implementation
    reconnaissance identified exactly three HTTP details this ADR had
    left genuinely unresolved (POST success status code, POST request
    body/solver-tuning exposure, and GET active's response for a valid
    school/year with no `Schedule` generated yet). All three are locked
    here, closing Decision #31 completely before Phase 3A3.4
    implementation begins.

    **Owner Decision A -- POST success is `201 Created`.**
    `POST /schools/{school_id}/years/{year_id}/schedule/generate`
    returns HTTP `201`, not `200`, on successful initial generation --
    the operation creates the canonical `Schedule` and its first
    `ScheduleVersion`, and Generate is initial-generation-only (a
    duplicate call conflicts with `409`, per Owner Decision 2), so `201`
    communicates resource creation more precisely than a generic `200`.
    No `Location` header is required or added in Phase 3A3.4: there is
    not yet a public, immutable, version-by-number URI to point at --
    `GET .../schedule/active` addresses only "whichever version is
    currently active," not a stable per-version resource, so it is not
    a correct `Location` target. The success response body is
    unchanged from the shape already locked above: exactly
    `version_number`, `solver_status`, `total_soft_penalty`,
    `created_at`, `is_active` -- no entries, no `wall_time_seconds`, no
    `random_seed`, no surrogate IDs, no `ordinal`, no CP-SAT telemetry.

    **Owner Decision B -- POST has no request body; solver tuning is
    not a public API input.** Phase 3A3.4's generation route calls
    `GenerateScheduleService.generate(...)` without supplying
    `solver_options`, so it always runs with the service's own default
    `SolverOptions()`. `max_time_seconds`, `num_search_workers`, and
    `random_seed` remain internal/application solver controls, never
    exposed over HTTP in this phase -- `GenerateScheduleService`'s
    existing optional `solver_options` parameter is unchanged and
    remains available for internal/test/future use; this decision does
    not remove or redesign that capability, it only declines to wire it
    to a public HTTP input. Exposing solver tuning as a product feature
    (e.g. a caller-supplied time budget) is separately scoped future
    work, not introduced by Phase 3A3.4.

    **Owner Decision C -- GET active before generation is `404`, with
    its own distinct (but still code-less) body; no new stable code is
    introduced.** For a valid School+AcademicYear configuration where
    `ScheduleVersionRepository.get_active_schedule` returns `None` (no
    canonical `Schedule` generated yet), `GET .../schedule/active`
    returns HTTP `404` with exact body `{"detail": "Active schedule not
    found"}` -- no `code` field. This is a **distinct** state from an
    unknown School/AcademicYear configuration, which keeps its existing
    exact compatibility contract unchanged: HTTP `404`, body
    `{"detail": "Scheduling configuration not found"}`, also with no
    `code` field. No `SCHEDULE_NOT_FOUND` (or any other new stable
    code) is introduced for either case -- the stable generation-specific
    `code` set defined above remains exactly `INVALID_CONFIGURATION`,
    `SCHEDULE_INFEASIBLE`, `SCHEDULE_ALREADY_EXISTS`, and nothing else;
    both 404 cases are distinguished by `detail` text alone, consistent
    with `ConfigNotFound` already being the explicit counterexample to
    the stable-`code` rule.

    **GET active's metadata shape** (resolving the reconnaissance's
    mechanical ambiguity): `GET .../schedule/active`'s success body
    carries the same public version-summary fields as the POST success
    body -- `version_number`, `solver_status`, `total_soft_penalty`,
    `created_at`, `is_active` (always `true` for this endpoint, since it
    only ever returns the currently-active version) -- plus `entries`,
    the flat, ordered entry list already locked above. Never exposed:
    `wall_time_seconds`, `random_seed`, any `schedule`/`schedule_version`
    surrogate DB ID, `ordinal`, or any CP-SAT-internal metadata. The
    already-locked flat entry contract (`source`, `day_id`, `period_id`,
    `requirement_id: str | None`, `reserved_block_id: str | None`,
    `activity_id`, `teacher_id`, `participant_group_id`, `resource_id`,
    `class_sections`, ordered by persisted `schedule_entry.ordinal`
    which itself is never public) is unchanged by this closure.

    With Owner Decisions A, B, and C locked, **Phase 3A3.4's HTTP
    contract has zero remaining owner decisions** -- implementation may
    proceed directly from this ADR without further product-owner input.

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

32. **Phase 3B ADR: first visual class timetable -- design locked,
    implementation not yet started.** Phase 3A3 (3A3.1-3A3.4) is CLOSED:
    a real `POST .../schedule/generate` and `GET .../schedule/active`
    exist on `main`, the latter deliberately generic/flat per Owner
    Decision 3 above. This entry locks the first Phase 3B slice's
    product/architecture decisions -- recovered from a dedicated
    pre-implementation reconnaissance -- before any frontend or new
    backend code is written.

    **Owner Decision 1 -- the backend owns the class-timetable
    projection; React does not reconstruct it.** `React` must not
    perform the full `/config` + `/schedule/active` grouping itself to
    build a class grid. Reason: current domain/solver behavior proves
    one `ClassSection`/day/period can have **more than one**
    `ScheduleEntry` simultaneously, because split `ParticipantGroup`
    branches (e.g. a German/Russian split) are constraint-synchronized
    to run in parallel at the same slot -- confirmed directly by solving
    `fixtures/valid_fixture.py`: its `german_8a`/`russian_8a` split
    requirements land at the identical three slots
    (`fri/p4`, `fri/p7`, `fri/p8`) for class `8a`. A naive
    one-entry-per-cell frontend reducer would silently lose one of the
    two real, simultaneous lessons. The locked data flow is:

    ```
    flat persisted schedule + SchedulingProblem/config
      -> backend APPLICATION-layer projection
      -> UI-shaped, read-only API response
      -> React renders the already-correct projection
    ```

    React may perform trivial presentation formatting only (e.g. layout,
    date/label formatting) -- never grouping/cardinality/domain
    resolution. This does not move any CP-SAT logic into the projection
    (the solve is already finished and persisted by the time this
    endpoint runs) and does not redesign persistence.

    **Owner Decision 2 -- the first projection route.**
    `GET /schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}`
    -- natural/domain IDs only, read-only, projecting the CURRENT active
    `ScheduleVersion` for one `ClassSection`. It is explicitly **not** a
    new schedule version, not a solver operation, not a reoptimization
    operation, not a generic schedule explorer, and not a history
    endpoint. Error compatibility: unknown school/year -> `404`
    `{"detail": "Scheduling configuration not found"}` (no `code`,
    identical to the existing contract); valid config but no active
    schedule -> `404` `{"detail": "Active schedule not found"}` (no
    `code`, identical to the existing `/schedule/active` contract);
    unknown `class_section_id` -> `404`
    `{"detail": "Class section not found"}` (no `code` -- no new stable
    error code is introduced for this state, consistent with the
    existing rule that only the three generation-specific outcomes
    carry one).

    **Owner Decision 3 -- cell cardinality is zero-or-more, never
    exactly one.** A class-timetable cell is **not** modeled as exactly
    one `ScheduleEntry`. One cell projects to zero-or-more entry rows
    structurally; for the current full-occupancy pilot, every
    instructional cell normally holds one-or-more. An ordinary lesson
    produces `cell.entries = [one entry]`; a German/Russian split
    produces `cell.entries = [German entry, Russian entry]` for class
    `8a`'s cell at their shared slots; a merged-group lesson (one entry
    whose `class_sections` spans multiple classes) still appears exactly
    once within *each* individual class's own projection. Multiple
    parallel entries in one cell must never be collapsed into one
    synthetic lesson. `run_poc.py`'s current per-class grid printer
    (`break`s on the first matching entry per cell) is explicitly **not**
    a correct Phase 3B reference implementation -- it predates, and does
    not handle, this parallel-entry case.

    **Owner Decision 4 -- parallel entries render as distinct visible
    sub-entries in the same cell.** Each parallel entry within one cell
    must expose enough to distinguish it from its siblings -- at
    minimum activity, the participant/subgroup label where applicable,
    and teacher where applicable. The first UI may use a stacked or
    side-by-side layout as a mechanical CSS choice, but must never hide
    one branch or replace distinct lessons with one ambiguous combined
    label.

    **Owner Decision 5 -- first-slice UI scope.** The first Phase 3B
    browser page contains only: one active-timetable page, a
    `ClassSection` selector, the timetable grid for the selected class,
    a loading state, a no-active-schedule state, a generic API-error
    state, and small active-version metadata only where useful. Excluded
    from this first slice: a Generate button, a school selector, an
    academic-year selector, a teacher timetable, schedule history,
    manual editing, locks, reoptimization, print/export, auth, config
    editing, dashboards, analytics. The first product milestone remains
    a REAL generated/persisted timetable rendered correctly in the
    browser -- never mock/static data as the integration proof.

    **Implementation note (no new owner decision -- product-owner
    locked, no phase number invented since this falls outside 3C.1-3C.5's
    own scope; IMPLEMENTED, REVIEWED (manually browser-reviewed by the
    product owner), COMMITTED, and MERGED to `main` at commit
    `a3bbcb8` -- CLOSED).** The "no Generate button" exclusion above was
    scoped to Phase 3B's first slice, never revisited by any Phase 3C
    decision (#33-#36 cover only teaching-assignment CRUD) -- until now:
    a minimal Generate trigger was added to `TimetablePage`'s existing
    no-schedule empty state, reusing the already-merged, no-request-body
    `POST .../schedule/generate` (Decision #31) exactly as built, with no
    backend/schema change. See `docs/PROJECT_STATE.md` for the full
    implementation record. Every other Owner-Decision-5 exclusion
    (school/year selectors, schedule history, manual editing, locks,
    reoptimization, print/export, auth, dashboards, analytics) remains
    exactly as excluded.

    **Implementation note (no new owner decision -- product-owner
    locked, no phase number invented; IMPLEMENTED, REVIEWED (manually
    browser-reviewed by the product owner), COMMITTED, and MERGED to
    `main` at commit `a8d8e75` -- CLOSED).** The "a teacher timetable"
    exclusion above is likewise revisited: a sibling read-only
    projection, `GET .../schedule/active/teachers/{teacher_id}`
    (`TeacherTimetableService`, mirroring `ClassTimetableService`'s
    exact architecture -- same two repository ports, same strict-lookup
    discipline, no new schema/migration), surfaced in `TimetablePage`
    via a `Class`/`Teacher` mode switch -- still one `/timetable` route,
    no new top-nav destination. Generate remains Class-mode-only, never
    duplicated. See `docs/PROJECT_STATE.md` for the full implementation
    record. Every remaining Owner-Decision-5 exclusion (school/year
    selectors, schedule history, manual editing, locks, reoptimization,
    print/export, auth, dashboards, analytics) remains exactly as
    excluded.

    **Owner Decision 6 -- School/AcademicYear are pilot-fixed; ClassSection
    is not.** For the first visual slice, `school_id`/`academic_year_id`
    are pilot-fixed, not selectors -- but never scattered as literal IDs
    throughout React components; the frontend receives them through
    exactly one replaceable configuration point (its concrete form --
    environment variable, config module, etc. -- is an implementation
    detail for the next slice, not decided here). This is a first-slice
    UI simplification only, not a change to the underlying multi-school
    domain architecture. `ClassSection`, by contrast, is never fixed --
    the class selector is populated dynamically from backend data.

    **Owner Decision 7 -- calendar derivation is data-driven, never
    hard-coded.** The projection must derive days from
    `SchedulingProblem.days` ordered by `.index`, and periods from
    `SchedulingProblem.periods` filtered to `is_instructional == True`
    and ordered by `.index` -- never a hard-coded Monday-Friday or
    periods 1-8. The current pilot naturally renders as 5x8 because its
    persisted config happens to have 5 ordered days and 8 instructional
    periods; lunch is not currently modeled as a `Period` at all in the
    pilot and is therefore not a grid row. A future school with a
    different calendar shape must not require rewriting the projection
    algorithm.

    **Owner Decision 8 -- the projection lives in the backend
    application layer, not React, ORM models, repository SQL, Pydantic
    schema code, or `api/serializer.py` treated as ad-hoc business
    logic.** The application-layer projection consumes already-detached
    `SchedulingProblem` and `ActiveScheduleVersion` and produces a plain,
    application-owned read/view model; the API layer then performs only
    explicit application-view-model -> Pydantic serialization, the same
    discipline `api/serializer.py` already applies elsewhere. The
    existing `SchedulingProblemRepository`/`ScheduleVersionRepository`
    ports remain sufficient in principle -- no repository method is
    added merely to perform presentation projection (Decision #12). The
    exact service/function/class name for this projection is an
    implementation detail for Phase 3B.1, not decided here.

    **Owner Decision 9 -- local frontend dev uses a Vite proxy, not
    CORS.** Phase 3B local development runs the Vite dev server proxying
    to the existing FastAPI server on `localhost:8000`; no `CORSMiddleware`
    is added to the backend solely for local development. Production
    frontend/static-serving deployment topology remains future work,
    out of scope for Phase 3B's first slice.

    **Owner Decision 10 -- minimal first frontend dependency set.** The
    first single-page timetable slice uses only React, TypeScript, and
    Vite. No React Router yet (a single page needs none). No
    Redux/Zustand/other state-management library (ordinary React
    state/effects suffice at this data volume/shape). No component
    library. Vitest + React Testing Library are introduced alongside the
    first real React component, not installed as unused scaffolding
    beforehand.

    **Phase 3B sub-slices** (locked sequence; each is its own
    implementation slice, never started early):
    - **3B.1** -- backend class-timetable projection: the application
      read/view model, the application projection logic/service, the
      dedicated class-projection endpoint (Owner Decision 2), and its
      backend tests.
    - **3B.2** -- frontend foundation: React/TypeScript/Vite scaffold,
      an API client, hand-written DTO types, the single school/year
      configuration point (Owner Decision 6), the Vite proxy (Owner
      Decision 9), with Vitest/React Testing Library introduced
      alongside the first real component (Owner Decision 10).
    - **3B.3** -- first real timetable page: dynamic `ClassSection`
      selector, the live projection endpoint, the timetable grid with
      parallel split entries shown correctly (Owner Decisions 3-4),
      loading/no-schedule/error states (Owner Decision 5). **This is
      the first visible milestone: a real generated/persisted class
      timetable rendered correctly in the browser.**
    - **3B.4** -- UX hardening: visual hierarchy, dense-grid
      readability, parallel-cell polish, laptop-width responsiveness,
      an accessibility/readability pass.

    No broader admin UI (manual editing, locks, reoptimization,
    schedule history, scenarios, auth, config editing, dashboards,
    analytics) is scoped into Phase 3B by this ADR.

    **Implementation note (Phase 3C.3a, no new owner decision --
    IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at commit
    `1499377`; 3C.3a CLOSED).** Owner Decision 10's "no React Router yet
    (a single page needs none)" was always conditioned on a single
    page; a second real page (Teaching Assignments) now exists, so
    `react-router-dom` (`^7.18.3`) is added -- the frontend's first and
    only dependency added since Phase 3B.2 -- with `BrowserRouter` (no
    concrete reason to prefer hash/memory routing given the existing
    Vite-dev-proxy/relative-URL deployment model), `/` redirecting to
    `/timetable`, and an explicit `*` not-found route rather than a
    silent fallback. The rest of Owner Decision 10 still holds
    unchanged: no Redux/Zustand/other state-management library (each
    page keeps its own component-local hooks, matching
    `TimetablePage`'s pre-existing pattern exactly, never a shared
    client-side cache/store), no component library, no other new
    dependency. The two real pages now render inside a new shared
    top-navigation shell (`components/AppShell.tsx`) rather than each
    duplicating a page chrome -- a compact top bar, not a sidebar,
    matching this product's identity as a professional scheduling tool
    rather than a generic school LMS, with no placeholder nav items for
    unbuilt future sections (School Setup, Constraints, ...). The new
    `pages/TeachingAssignmentsPage.tsx` is read-only in 3C.3a: it
    consumes Phase 3C.2b's `GET .../teaching-assignments` projection
    directly via a new `api/teachingAssignments.ts` module (never
    reconstructed from `/config`), rendering a teacher-workload table,
    an assignments table, a friendly-labeled "Advanced" badge for every
    non-`editable` row (backend `advanced_reasons` codes mapped to
    presentation-only labels, e.g. `fixed_placement` -> "Fixed
    placement" -- never a change to backend semantics, and an
    unrecognized future code falls back to its raw form rather than
    breaking render), and an informational (not error-styled)
    `configuration_locked` banner. No create/edit/delete controls exist
    yet, not even disabled ones -- that interaction belongs to Phase
    3C.3b. No backend/schema change; Alembic head unchanged at
    `01b2ae564170`.

    **Implementation note (Phase 3C.3b, no new owner decision --
    IMPLEMENTED, REVIEWED (manually browser-reviewed by the product
    owner against a real unlocked review dataset), COMMITTED, and
    MERGED to `main` at commit `f608b7d`; 3C.3b CLOSED).** Pure frontend
    consumer of the write contract Decisions #34-#36 already locked --
    no new backend/schema/owner decision. Create/edit/delete now exists
    for plain, editable `WHOLE_CLASS` assignments only, via a right-side
    modal drawer (Add/Edit) and inline per-row confirmation (Delete);
    advanced rows never gain mutation controls, and the global
    `configuration_locked` lock separately disables (never hides)
    Edit/Delete on plain rows, sharing the existing lock banner as its
    one explanation -- the two disabled-looking states are deliberately
    never conflated. Every successful mutation triggers one
    authoritative re-fetch of the unified GET projection; a write that
    succeeds but whose follow-up re-fetch fails is never reported as
    failed (the existing projection stays visible, marked stale, with
    mutations disabled until `Retry` succeeds); a `409
    SCHEDULING_CONFIGURATION_LOCKED` stale-client race closes the
    initiating surface and re-fetches into the now-genuinely-locked
    state rather than a false success. `ApiError` gained additive
    `code`/`body` fields for the already-locked structured error
    contract; `detail` stays a safe string always. State remains
    component-local hooks; no Redux/Zustand/query library. No
    backend/schema change; Alembic head unchanged at `01b2ae564170`.
    **With 3C.3a and 3C.3b both CLOSED, Phase 3C.3 (Teaching
    Assignments frontend milestone) is complete.**

    With Owner Decisions 1-10 locked, **Phase 3B.1 has zero remaining
    owner decisions** -- implementation may proceed directly from this
    ADR without further product-owner input.

33. **Owner Decision -- `ParticipantGroup` gains an authoritative
    semantic role; the role is NEVER inferred (Phase 3C, design locked,
    implementation not yet started).** Admin configuration needs to
    distinguish "this assignment targets the whole class" from "this
    assignment targets a subgroup" or "this assignment spans multiple
    classes" -- a real domain requirement first surfaced, and
    deliberately deferred, during Phase 3B.4's UX review (see
    `PROJECT_STATE.md`), now formally decided.

    **The three approved roles**, added as a new mandatory
    `ParticipantGroup.role` field (a plain string enum, matching this
    codebase's existing enum style, e.g. `EntrySource`/`SolverStatus` --
    not a new object type):
    - **`WHOLE_CLASS`** -- the full population of exactly one
      `ClassSection`. Ordinary whole-class `TeachingRequirement`s target
      this group. Invariant: exactly one `WHOLE_CLASS` group per
      `ClassSection` per `AcademicYear`.
    - **`SUBGROUP`** -- a subset/branch of exactly one `ClassSection`
      (e.g. "8-A German", "8-A Russian"). Never implies the whole class,
      regardless of whether it happens to be linked to a split via
      `TeachingRequirement.split_group_id`.
    - **`MERGED_CLASSES`** -- spans two or more `ClassSection`s (e.g. a
      combined 9-A+9-B history lesson).

    **Cardinality invariants** (distinct `class_sections` membership,
    never raw tuple length -- a duplicated entry, e.g. `("c1", "c1")`,
    must never let `MERGED_CLASSES` appear valid merely because the
    tuple has length 2; preflight rejects the duplicate itself on its
    own, independent of persistence's existing `UNIQUE` constraint on
    the same concern):
    `WHOLE_CLASS` and `SUBGROUP` both require exactly 1 distinct class
    section; `MERGED_CLASSES` requires 2 or more distinct class
    sections. These are per-row invariants,
    owned exclusively by **`validation.preflight`** (matching how
    split-group consistency is already validated today), never by
    `ParticipantGroup` itself -- the dataclass stays a **plain frozen
    data holder**, consistent with every other domain object in this
    codebase; no `__post_init__`/self-validating construction pattern
    is introduced (this codebase channels all structural/cross-row
    validation through `validation/preflight.py`, never scattered
    across dataclasses). Nor is this a database `CHECK` constraint,
    since a `CHECK` on the `participant_group` row cannot see the count
    of child `participant_group_class_section` rows without a trigger,
    and this codebase already has an established, explicit preference
    for application-level invariant enforcement over database-mechanism
    ones for exactly this kind of cross-row concern (see #21, and #31's
    "no PostgreSQL trigger... unjustified machinery" reasoning). The DB
    still owns one thing here: a row-local `CHECK` that `role` is one of
    the three valid values (matching the existing `TeacherAvailability.
    status`/`Activity.kind` convention) -- value-validity is DB-owned,
    cardinality is preflight-owned, never duplicated across both.

    **"Exactly one `WHOLE_CLASS` group per `ClassSection` per
    `AcademicYear`" is likewise enforced by `validation.preflight`, not
    a new database structure, for now.** 3C.1 evaluated the previously
    "recommended" nullable denormalized `whole_class_of_class_section_id`
    column + partial unique index and **decided against adding it**:
    `ParticipantGroup` remains read-only reference data through the
    entirety of 3C.1-3C.4 (Decision #34), so no write path exists yet
    that could ever violate this invariant except the one-time,
    developer-controlled fixture reseed -- building real DB machinery
    to defend a write path that doesn't exist yet would be exactly the
    premature abstraction this project has consistently avoided (#12,
    #29, #31). This remains open to revisit once a future
    `ParticipantGroup` write service (3C.5+) actually needs
    transactional protection against concurrent creates; until then,
    preflight is the sole enforcement point, and any future write
    service re-runs the same preflight function rather than
    reimplementing the rule.

    **Never inferred -- reaffirming and closing the Phase 3B.4 gap
    authoritatively.** The system must never derive `role` from: the
    group's name (no "All of" or any other string pattern); any
    number/name pattern; `TeachingRequirement.split_group_id`
    (presence/absence of a split link is evidence of *usage*, not of
    *role* -- a `SUBGROUP` need not be linked to any split); or
    `class_sections` length alone (length 1 is necessary but not
    sufficient to distinguish `WHOLE_CLASS` from `SUBGROUP` -- both
    share it). The Phase 3B.4 owner decision rejecting UI string/name
    heuristics remains valid and unchanged; this decision closes the
    same gap in the domain instead of leaving it unsolved in React.

    **Backfill rule, as implemented by 3C.1.** Existing `ParticipantGroup`
    rows (today, only ever created by the TEST-ONLY aggregate writer
    against `fixtures/valid_fixture.py`) are **not** classified
    heuristically by the migration. `ParticipantGroup.role`
    (`persistence/models.py`) is `TEXT NOT NULL` with a `CHECK` for the
    three values, added with **no `server_default` and no in-migration
    backfill logic whatsoever** -- deliberately fail-closed: the
    migration (`01b2ae564170`) raises against any `participant_group`
    table that already has rows, since there is no safe, non-heuristic
    way for generic migration code to assign an authoritative role to a
    row it knows nothing about. `fixtures/valid_fixture.py`,
    `fixtures/school_scale/curriculum.py`, and
    `tests_web/support/problem_writer.py` were updated to set `role`
    explicitly and deterministically from each fixture's own authorial
    intent (`pg_8a`/`pg_8b`/`pg_9a`/`pg_9b` -> `WHOLE_CLASS`;
    `pg_8a_german`/`pg_8a_russian` -> `SUBGROUP`; `pg_9a_9b_merged` ->
    `MERGED_CLASSES`, and the school-scale generator's own
    `whole_class_group()`/split/merged call sites analogously) -- never
    a generic string-matching pass. The local dev database (the only
    place any `ParticipantGroup` row existed) was backed up, then its
    pilot config/schedule was cleared (via cascading delete from the
    `School` row) before the migration was applied, then re-bootstrapped
    from the now-role-aware fixture through the same TEST-ONLY writer,
    then a real schedule was regenerated through the production
    `POST .../schedule/generate` path -- no pilot natural ID or
    heuristic was ever embedded in the migration file itself.

34. **Owner Decision -- the first Phase 3C admin MVP is Teaching
    Assignments/Workload, narrowly scoped to ordinary `WHOLE_CLASS`
    assignments (design locked, implementation not yet started).** The
    primary model: `Teacher -> Class/ParticipantGroup -> Subject/
    Activity -> Weekly periods`, backed by the existing
    `TeachingRequirement.weekly_periods` field -- no new domain concept
    needed for the assignment record itself (confirmed structurally
    sufficient by the Phase 3C reconnaissance).

    **Editable in the first slice**: `teacher_id`, the target
    `participant_group_id` (constrained to the `ClassSection`'s
    canonical `WHOLE_CLASS` group only, per Decision #33), `activity_id`,
    `weekly_periods`. Create/update/delete one plain ordinary
    `TeachingRequirement`.

    **`SUBGROUP`/`MERGED_CLASSES` must exist correctly in the domain
    (Decision #33) but their editing workflows are explicitly
    deferred** -- split synchronization (two-or-more linked
    requirements sharing a `split_group_id`, matching shape/
    weekly_periods) and merged-group configuration both add real
    validation/UX complexity beyond this slice's scope. The first-slice
    write path must **never** silently treat a `SUBGROUP`/
    `MERGED_CLASSES`-targeted requirement as an ordinary assignment --
    a request targeting a non-`WHOLE_CLASS` group through this narrow
    write path must be rejected, never silently accepted.

    **Explicitly read-only/deferred in the first slice** (existing
    domain defaults may be used for newly-created plain requirements
    only where those defaults already exist in the domain today -- no
    new default is invented by this decision): block policy editing
    (new requirements use the domain's existing
    `LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)` default -- already
    the dataclass default today, not a new one), distribution policy
    editing (existing `DistributionPolicy()` default, both fields
    already optional), time preferences (existing `time_preferences:
    tuple = ()` default), resource requirements (existing
    `resource_requirement: None` default), teacher availability
    editing, fixed placements, reserved blocks, the split-group
    assignment workflow, the merged-group assignment workflow, and
    teacher contractual/target workload (below).

    **Assigned vs. contractual workload -- kept as two distinct,
    separately-timed product concepts.**
    - **Assigned workload** (in the first MVP): `SUM(TeachingRequirement.
      weekly_periods) GROUP BY teacher_id`. Verified safe to derive this
      way by the Phase 3C reconnaissance: no double-counting from split
      branches (distinct teachers/activities in every observed case,
      and the hard teacher-non-overlap constraint prevents same-teacher
      double-booking in any *feasible* configuration regardless), no
      double-counting from merged-group requirements (one row, counted
      once), and block shape never affects the total (a hard equality
      constraint in `scheduling/model_builder.py`). Whether
      `ReservedBlock.teacher_id` assignments should also count toward a
      displayed "occupied periods" figure is an explicit
      **out-of-scope** question for this MVP -- the approved scope is
      `TeachingRequirement`-only, matching the product owner's literal
      request.
    - **Contractual/target/capacity workload** (e.g.
      `target_weekly_periods`, `contracted_weekly_periods`,
      `max_weekly_periods`) -- **does not exist anywhere in the current
      domain** (confirmed by the Phase 3C reconnaissance: the only
      `capacity`-named field in the entire codebase is the unrelated
      `Resource.capacity`). **Not part of the first Phase 3C MVP.** No
      such field is added by this decision. Recorded only as a
      **future workload-management product capability**, to be
      designed as its own decision if/when a concrete need appears
      (matching this codebase's consistent YAGNI discipline, #12).

    **Duplicate-assignment semantics.** For the plain `WHOLE_CLASS`
    write path, the first write service must reject a second ordinary
    `TeachingRequirement` with the identical `(teacher_id,
    participant_group_id, activity_id)` triple within one
    `AcademicYear`. This check is **application-level only, not a
    database uniqueness constraint** -- a blanket DB-level uniqueness
    constraint across *all* `TeachingRequirement` rows would be too
    restrictive: split-branch siblings already differ in at least
    teacher/activity/group in every observed case, but nothing in the
    domain model rules out a legitimate future pattern needing two
    requirement rows sharing that triple (e.g. two differently
    time-distributed portions of one teacher's load for one
    class/activity). Scoping the duplicate check to application code --
    and, for now, to plain ordinary (non-split, `WHOLE_CLASS`-targeted)
    requirements specifically -- keeps that door open without a schema
    change.

35. **Owner Decision -- scheduling configuration is write-locked once an
    `AcademicYear` has a generated `Schedule` (Phase 3C MVP, design
    locked, implementation not yet started).** Once
    `ScheduleVersionRepository.get_active_schedule(...)` (the existing
    port method already used by `GenerateScheduleService` to reject a
    second `Generate` call) returns non-`None` for an `AcademicYear`,
    every Phase 3C configuration-write operation for that year must be
    **rejected outright**, never merely warned about. Reason: without
    this gate, a generated `ScheduleVersion`'s denormalized display
    joins -- already a known, accepted MVP limitation (#31: a
    historical version re-reads *current* teacher/activity/policy
    values, not the values true at generation time) -- would silently
    drift arbitrarily far from what was actually solved, with no way
    for a viewer to know.

    **For this MVP, explicitly**: no `STALE`/`OUTDATED` schedule state;
    no automatic invalidation of an existing schedule on a rejected
    write attempt; no silent configuration mutation after generation
    (the write is refused outright, never partially applied); no
    configuration snapshot/version model; no regenerate/re-optimize
    lifecycle. The **future** direction -- edit configuration -> the
    existing schedule becomes `OUTDATED` -> generate/re-optimize a new
    immutable `ScheduleVersion` -> the new version becomes active -- is
    explicitly deferred and **must not be implemented** as part of the
    first Phase 3C MVP. The first write architecture only needs to
    **leave room** for it: the write-gate check itself (one "does this
    year already have a schedule" test at the top of every future write
    operation) is the one piece of scaffolding this MVP needs, and it
    becomes the future trigger point for marking a schedule `OUTDATED`
    rather than simply refusing the write, with nothing about the check
    itself needing to be redesigned later.

    **Enforcement boundary**: the backend application service, never
    React alone. Every future configuration-write use case (the
    teaching-assignment service first) must perform this check itself
    before touching any row, mirroring exactly the same defensive
    pattern `GenerateScheduleService` already uses for its own "a
    schedule already exists" 409 rejection -- reusing the existing
    `ScheduleVersionRepository.get_active_schedule` port method, adding
    no new repository method for this specific check. Recommended
    future error contract (not implemented by this decision): a new
    application-level error, e.g. `ConfigurationLockedError`, carrying
    the natural `school_natural_id`/`academic_year_natural_id`, mapped
    to **HTTP 409 Conflict** with a plain-text `detail` (e.g.
    "Configuration is locked: a schedule has already been generated for
    this academic year.") -- matching the existing
    `ScheduleAlreadyExistsError` -> 409 precedent (#31), not a new or
    nonstandard status code.

    **Frontend routing (informative, not itself a locked owner
    decision).** Phase 3B intentionally shipped with no React Router,
    conditioned explicitly on there being a single page (#32 Owner
    Decision 10). Phase 3C introduces a second, genuinely separate
    product area (viewing a timetable vs. editing configuration) that a
    real admin would want to navigate between via bookmarkable,
    back-button-friendly URLs -- that condition no longer holds.
    Recommendation: introduce a lightweight React Router in 3C.3 with
    two routes (`/timetable`, `/configuration/teaching-assignments`),
    each page still managing its own local fetch/state -- Redux/Zustand
    remain unjustified (#32 Owner Decision 10's reasoning still holds:
    no cross-page shared client state exists yet). This is a
    recommendation for 3C.3 to confirm, not a locked requirement of
    this ADR.

    **Reference-data boundary (informative).** For the first Teaching
    Assignments slice, `Teacher`, `ClassSection`, `Activity`, and each
    `ClassSection`'s canonical `WHOLE_CLASS` `ParticipantGroup` remain
    pre-existing, read-only reference data -- full CRUD for
    teachers/classes/activities/participant groups/calendar/
    availability/constraints/resources is real, needed future product
    work, but is explicitly **not** dragged into this first slice
    unless a concrete blocker forces it.

    **Recommended Phase 3C sub-slices** (sequencing guidance, adjust if
    a later slice's own reconnaissance requires it -- not itself a
    locked owner decision beyond #33-#35):
    - **3C.1** -- `ParticipantGroup` role domain/persistence contract:
      the role enum, the invariants above, the migration, mapper/read-
      API updates (`/config`'s existing response gains the field), the
      fixture/dev-data explicit (non-heuristic) classification, and
      tests. No admin UI.
    - **3C.2** -- teaching-assignment write backend: the application
      write port/service, create/update/delete for plain `WHOLE_CLASS`
      `TeachingRequirement`s, duplicate validation, the Decision #35
      schedule-exists write gate, an assigned-workload read model/
      summary, API routes/schemas, and tests. No frontend editor.
    - **3C.3** -- configuration frontend foundation: the routing
      decision (above), the Teaching Assignments page, loading
      reference choices, the table, the add/edit/delete form, surfacing
      backend validation errors, and the assigned total.
    - **3C.4** -- manual browser UX hardening: empty state,
      loading/error states, workload-summary readability,
      destructive-action confirmation, accessibility, real
      PostgreSQL/API/browser proof (mirroring the 3B.3/3B.4 precedent).
    - **3C.5** -- Phase 3C closure + the broader configuration roadmap
      (teachers/classes/activities/groups themselves becoming editable,
      subgroup/merged-group editors, calendar/availability/constraints
      editors) -- explicitly future, not decided now.

    **Explicitly out of scope for the whole Phase 3C admin milestone**:
    authentication/authorization (flagged, not built, unless
    unavoidable to gate writes at all); a teacher timetable view;
    manual schedule editing; locks/reoptimization UI; a schedule
    history UI; print/export; a mobile redesign; configuration
    versioning; the stale-schedule lifecycle; the regeneration
    workflow; teacher contractual workload; all-entity CRUD in one
    milestone; a subgroup editor; a merged-group editor; and an
    advanced block/distribution/time-preference editor.

    With Owner Decisions #33-#35 locked, **Phase 3C.1 has zero
    remaining owner decisions** -- implementation may proceed directly
    from this ADR without further product-owner input.

36. **Owner Decision -- the generation-vs-config-write race is closed by
    a short `AcademicYear` row lock plus a final reload-and-compare, no
    config-revision schema, never a DB transaction held across CP-SAT
    solving (LOCKED; Phase 3C.2a is IMPLEMENTED, REVIEWED, COMMITTED,
    and MERGED to `main` at commit `2f4f9e6` "feat: add teaching
    assignment write backend").** Phase 3C.2's reconnaissance
    (informative note above Decision #35) identified a real race:
    `GenerateScheduleService`
    loads the scheduling configuration, solves (which can take seconds),
    and only then persists -- if a Phase 3C.2 configuration write lands
    in between, the persisted `ScheduleVersion` would silently reflect a
    configuration that was never actually solved. Explicitly rejected
    resolutions: a config-revision/schema counter; a snapshot/version
    model; a generation-in-progress table/state; an advisory lock held
    across the whole solve; a long-lived SQLAlchemy session spanning the
    solver call; silently accepting the stale result as an MVP
    limitation (unlike Decision #35's *display*-staleness, this would be
    a *correctness* defect -- persisting a schedule that does not match
    what was solved).

    **The locked mechanism**: every Phase 3C configuration-write
    transaction (`SqlAlchemyTeachingAssignmentRepository.create`/
    `update`/`delete`) and `GenerateScheduleService`'s final persist step
    (`SqlAlchemyScheduleVersionRepository.persist_initial_version`)
    serialize through the *same* single primitive -- a `SELECT ...
    FOR UPDATE` on the target `AcademicYear` row, acquired only inside a
    short, ordinary transaction, never held across CP-SAT solving.
    Generation's flow: load the configuration -> preflight -> solve (no
    open session/transaction across this step) -> verify -> open a new
    transaction -> lock the `AcademicYear` row -> reload the current
    scheduling configuration under that lock -> compare it, by plain
    dataclass equality, against the exact configuration the solver used
    -> if different, roll back and raise a new, retryable
    `ConfigurationChangedDuringGenerationError` (zero rows persisted) ->
    if unchanged, recheck the Decision #35 schedule-exists gate (not
    solely trusting the earlier pre-solve precheck), then persist and
    commit while still holding the lock. Every Phase 3C.2 write
    (`create`/`update`/`delete`) acquires the identical row lock first,
    then reloads and re-validates the configuration authoritatively
    under that lock (the same pure `application/`-owned rule functions
    used for an earlier, un-locked fast-fail check, packaged as a
    `Callable[[SchedulingProblem], None]` so the two checks can never
    diverge) before writing -- so a writer that acquires the lock while
    generation is mid-solve simply blocks until generation's commit
    releases it, then observes the just-persisted schedule and is
    rejected under Decision #35's existing `ConfigurationLockedError`
    gate; a writer that lands and commits first is what generation's own
    reload-and-compare then detects and aborts on. The identical lock
    also serializes the pre-existing duplicate-create race (two
    concurrent creates of the same `(teacher, participant_group,
    activity)` triple) without any new database `UNIQUE` constraint.

    **Why plain dataclass equality suffices**: `SchedulingProblem` is
    already an immutable, `@dataclass(frozen=True)` tree of frozen
    dataclasses and tuples with an auto-generated structural `__eq__` --
    comparing the freshly-reloaded configuration against the exact
    object the solver ran against needs no bespoke fingerprint/hash
    function; any actual difference in any field, at any depth, makes
    the two unequal.

    **Explicitly rejected for this decision** (restated for
    emphasis): no config-revision column or schema model of any kind;
    no snapshot/version table; no generation-in-progress marker; no
    advisory lock spanning the solve; no long-lived session across
    solving; no silent acceptance of a stale persisted schedule as an
    MVP limitation.

    **Scope note**: this decision closes the concurrency-correctness
    half of Phase 3C.2 (3C.2a), now CLOSED. It implements no HTTP write
    routes, no read/workload projection, and no frontend -- those remain
    **Phase 3C.2b**.

    **Implementation note (Phase 3C.2b, no new owner decision --
    IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at commit
    `9570358`; Phase 3C.2b CLOSED; Phase 3C.2 backend/API milestone now
    complete).** A read-only technical contract gate
    preceding implementation found **zero** genuine owner decisions
    remaining: every open question (route shape, request/response
    contracts, error-code mapping, ordering, empty/partial-state
    behavior) resolved directly from this ADR and the codebase's
    existing house style. The locked HTTP contract, now implemented
    exactly as gated:
    - `GET/POST /schools/{school_id}/years/{year_id}/teaching-assignments`
      and `PUT/DELETE .../teaching-assignments/{requirement_id}` -- no
      `PATCH` (the write semantics were always full-replacement); no
      separate workload endpoint (one page-oriented `GET` projection).
    - The `GET` projection is built by a new, dedicated, read-only
      `TeachingAssignmentsProjectionService` (`application/`) --
      deliberately kept separate from the write-only
      `TeachingAssignmentService`, mirroring the existing
      `ClassTimetableService`/`GenerateScheduleService` read/write
      split. It reuses `teaching_assignment_rules.plain_reasons`
      directly for `editable`/`advanced_reasons` on every requirement
      (never a reimplemented predicate, so a `FixedPlacement` reference
      affects editability automatically) and
      `ScheduleVersionRepository.get_active_schedule(...) is not None`
      directly for `configuration_locked` (the same Decision #35 gate,
      no new port method). It returns **every** `TeachingRequirement`
      (plain and advanced alike) plus backend-owned reference-data
      lists (`teachers`, `activities`) and the authoritative
      `whole_class_targets` mapping (`ClassSection` -> its canonical
      `WHOLE_CLASS` `ParticipantGroup`, built from `role`/
      `class_sections` alone, never a name/count heuristic; a
      `ClassSection` whose Decision #33 invariant is broken is simply
      omitted -- this projection is not that invariant's enforcement
      point) -- so the frontend can never infer, reconstruct, or guess
      any of this. `teacher_workloads` sums **every** requirement type
      per teacher (never only editable ones), including teachers with
      zero requirements (`total_weekly_periods: 0`).
    - `POST`/`PUT` return only `{"id", "warnings"}`; `DELETE` returns
      `{"deleted_id", "warnings"}` (200, never 204) -- a mutation never
      returns the full saved-row projection, since the caller must
      re-fetch the unified `GET` afterward anyway (workload totals,
      ordering, and lock state may all have changed). `warnings` reuses
      the existing `ValidationDiagnosticResponse` shape verbatim.
    - Error mapping: `SchedulingProblemNotFoundError`/
      `TeachingAssignmentNotFoundError` -> code-less 404s, matching the
      existing house style exactly; `UnknownReferenceError`/
      `NonWholeClassTargetError`/`InvalidTeachingAssignmentError` -> 422
      with a stable `code`; `AdvancedRequirementNotEditableError`/
      `DuplicateTeachingAssignmentError`/`ConfigurationLockedError` ->
      409 with a stable `code` -- `SCHEDULING_CONFIGURATION_LOCKED` is
      now the locked Decision #35 HTTP contract.
    - Corrected an existing gap: `ConfigurationChangedDuringGenerationError`
      (Decision #36) was previously uncaught by
      `POST .../schedule/generate` and would have leaked as a generic
      500 the first time it could actually occur in production. It now
      maps to 409 `CONFIGURATION_CHANGED_DURING_GENERATION`, reusing
      the existing `GenerationErrorResponse` shape -- no other generate
      error semantics changed.
    - No persistence schema change; Alembic head unchanged at
      `01b2ae564170`. No frontend/React Router work -- Phase 3C.3 is
      still not started.

37. **Owner Decision -- Teacher identity is `first_name`/`last_name`,
    never a single `name` (LOCKED; Real-School Setup MVP Slice A is
    IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at commit
    `4775684` "feat: split teacher name fields" -- CLOSED. Not
    pushed.).**
    The domain `Teacher` gains
    `first_name: str` and `last_name: str` as **both required**
    constructor arguments -- no dataclass default papers over the old
    single-`name` shape, and every construction site across `src/`,
    `tests/`, and `tests_web/` was migrated to pass both explicitly.
    Migrated legacy rows may legitimately carry an explicit
    `last_name=""` (the migration backfills it that way, non-
    heuristically, to preserve the old single-token display name
    byte-for-byte) -- that is a fact about persisted data, never a
    constructor convenience; the future Teacher CRUD (Slice B) owns
    real create/update input validation for genuinely new teachers.
    This replaces the former single `name` field, plus a derived
    `full_name` property (trims each part,
    joins non-empty parts with exactly one space, never a trailing or
    double space) that is now the *one* authoritative display name.
    Every existing user-facing read contract that used to expose the
    old single `name`/`teacher_name` string (`GET /config`'s
    `TeacherResponse`, Teaching Assignments' `teacher_name` and
    `TeacherWorkload.teacher_name`, Class Timetable's `teacher_name`,
    Teacher Timetable's top-level `teacher_name`) continues to expose
    exactly one resolved string, now sourced from `teacher.full_name`
    -- Slice A adds zero first_name/last_name fields to any public API
    response (that is Slice B's job, if and when it happens) and
    changes zero frontend files (confirmed: `git diff --stat --
    frontend/src` empty).

    The ORM `teacher` table gains `first_name TEXT NOT NULL`/
    `last_name TEXT NOT NULL`, replacing the dropped `name` column, via
    a new Alembic migration (`cae76cba3c58`, `down_revision =
    '01b2ae564170'`) that is deliberately staged and reversible --
    unlike Decision #33's fail-closed `role` migration, this one has
    real pre-existing data to preserve: add both columns nullable ->
    backfill (`first_name = name`, `last_name = ''`, scoped to
    `teacher` only) -> `NOT NULL` -> drop `name`; downgrade reverses
    this exactly, rebuilding `name` via the identical whitespace-safe
    trim-and-join SQL that mirrors `full_name`'s own logic, so every
    pre-existing synthetic teacher name (`"Teacher Math"`, `"Teacher
    History"`, `"Teacher German"`, ...) round-trips byte-for-byte
    through upgrade -> downgrade -> upgrade. No heuristic first/last
    splitting was attempted or wanted. Live-validated this session
    against the real dev database (`school_timetable`, both the
    canonical `synthetic-school`/`ay-2026` pilot and the local-only
    `synthetic-review-school`/`ay-review-2026` review dataset, backed
    up via `pg_dump` beforehand) and the separate test database: the
    running app's `GET /config`, teaching-assignments, class-timetable,
    and teacher-timetable endpoints all confirmed to return the exact
    pre-migration teacher names, unchanged, after upgrade; the
    downgrade/upgrade round trip confirmed exact on the dev database;
    both databases left on the new head. `tests_web` 147/147 and the
    core `tests -m "not slow"` suite 199/5 (194 pre-existing + 5 new
    `tests/test_people.py` cases covering `full_name` with a real
    first+last, an explicit empty `last_name`, whitespace trimming,
    `last_name` being a required constructor argument (a missing
    `last_name` raises `TypeError`), and pre-migration name
    preservation) both green; frontend 185/185 green, build clean.

    Explicitly out of scope for Slice A (deferred to a later slice, not
    decided here): Teacher CRUD endpoints/write services, exposing
    `first_name`/`last_name` on any public API response, Classes/
    Subjects CRUD, School Setup UI, real-school data entry, any change
    to `TeachingAssignment` semantics, the solver, or auth/user
    integration. This decision record covers Slice A only -- **the
    broader Real-School Setup MVP is NOT complete.** Next slice per the
    approved setup contract: **Slice B -- reference-data write
    foundation + Teacher CRUD** (not started, not designed in detail
    here).

    **Slice B (Teacher CRUD + shared reference-data write foundation)
    update: IMPLEMENTED, REVIEWED, live API reviewed, COMMITTED, and
    MERGED to `main` at commit `6fb4bed` "feat: add teacher CRUD" --
    CLOSED. Not pushed. Zero new Owner Decisions
    required** -- every open question was resolved from direct repository
    precedent (Decision #34's write-port shape, Decision #35's lock
    reuse, Decision #36's race-safety reuse), not a new product
    decision. `GET/POST /schools/{school_id}/years/{year_id}/teachers`
    and `PUT/DELETE .../teachers/{teacher_id}` -- `TeacherService`
    (`application/teacher_service.py`) create/update/delete, shaped
    exactly like `TeachingAssignmentService`: a fast un-locked
    configuration-lock precheck, then the authoritative,
    lock-protected recheck inside `SqlAlchemyTeacherRepository`
    (`persistence/teacher_repository.py`). The `AcademicYear`
    resolve/lock/Decision-#35-recheck sequence -- previously private to
    `teaching_assignment_repository.py` -- is now shared, unchanged,
    via `persistence/configuration_write_lock.py` (persistence-private;
    `_natural_to_surrogate`/ordinal helpers deliberately stay local to
    each repository, never generalized into a model-agnostic
    framework); `TeachingAssignmentRepository`'s own behavior is
    unchanged by this pure refactor (its full test suite still green).
    Teacher natural IDs are always server-generated
    (`teacher_<uuid4().hex>`), never client-supplied. `first_name`/
    `last_name` are trimmed and a blank result after trimming is
    rejected (`InvalidTeacherError`, `422 INVALID_TEACHER`) -- no
    uniqueness, no ASCII-only or alphabet-only restriction, no new
    max-length beyond the existing unbounded `Text` column convention;
    same-name teachers are explicitly allowed. Deletion is rejected
    (`TeacherInUseError`, `409 TEACHER_IN_USE`, `referenced_by` naming
    every referencing kind in the deterministic order
    `TEACHING_REQUIREMENT`/`TEACHER_AVAILABILITY`/`RESERVED_BLOCK`)
    whenever the *current* configuration still references the teacher
    -- an application-level rule enforced even where the underlying
    `teacher_availability` FK is `ON DELETE CASCADE`, never relying on
    the database to silently cascade meaningful configuration away;
    `teaching_requirement`/`reserved_block`'s own `RESTRICT` FKs remain
    structural backstops only. Reuses `ConfigurationLockedError`/
    `TeacherNotFoundError` verbatim -- no Teacher-specific lock code, no
    second not-found exception. Zero schema/migration impact (Alembic
    stays at `cae76cba3c58`); zero frontend production changes. Test
    gate: 23 new pure `tests/test_teacher_service.py` cases (zero DB);
    23 new `tests_web/test_teacher_api.py` HTTP-contract cases plus 9
    new `tests_web/test_teacher_repository.py` cases (including a
    deterministic, sequential proof of both generation-vs-write race
    orderings, mirroring `test_teaching_assignment_repository.py`'s own
    proof exactly). Live-validated against a new, local-only, unlocked
    `teacher-crud-review-school`/`ay-teacher-crud-2026` dataset
    (retained, no `Schedule` generated) as well as the running app's
    real HTTP API -- create/read/update/delete, same-name coexistence,
    and the `TEACHER_IN_USE`/`SCHEDULING_CONFIGURATION_LOCKED` error
    contracts all confirmed; both canonical datasets
    (`synthetic-school`/`ay-2026`, `synthetic-review-school`/
    `ay-review-2026`) reconfirmed unchanged throughout. This entry
    records Slice B's implementation status only -- **the broader
    Real-School Setup MVP remains NOT complete.** Next slice per the
    approved setup contract: **Slice C -- Classes CRUD + canonical
    `WHOLE_CLASS` lifecycle** (not started, not designed in detail
    here).

    **Slice C (Classes CRUD + canonical `WHOLE_CLASS` lifecycle)
    update: IMPLEMENTED, REVIEWED, live API reviewed, test-
    infrastructure correction reviewed, COMMITTED, and MERGED to
    `main` at commit `460e42f` "feat: add class CRUD" -- CLOSED. Not
    pushed. Zero new Owner Decisions
    required** -- Owner Decision #33 (canonical `WHOLE_CLASS`
    cardinality) already settled the one candidate architectural fork
    (an explicit DB-level canonical-group FK vs. reusing the existing
    role+membership model): the current schema is reused unchanged, no
    migration. `POST /schools/{school_id}/years/{year_id}/classes`
    creates a `ClassSection` together with its owned, internal
    canonical `WHOLE_CLASS` `ParticipantGroup` and the one membership
    linking them, atomically, under the exact same `AcademicYear` lock/
    Decision-#35-recheck sequence Teacher and Teaching Assignment
    writes already use (`persistence/configuration_write_lock.py`,
    reused entirely unchanged -- confirmed by its two existing
    consumers' full test suites staying green). The administrator never
    supplies or manages the canonical group; both natural IDs
    (`class_<uuid4().hex>`/`group_<uuid4().hex>`) are always
    server-generated via explicit, independently-injectable factories.
    `name` is trimmed with a blank result rejected
    (`422 INVALID_CLASS`); duplicate `ClassSection` names within one
    `AcademicYear` are rejected by exact, case-sensitive, trimmed
    comparison (`409 DUPLICATE_CLASS`) -- an application rule, no new
    DB uniqueness. For classes created/renamed through this new path,
    the canonical group's display name is kept in sync with the
    class's own name (`8-A` -> canonical group `8-A`); pre-existing
    fixture-seeded canonical groups keep their historical `"All of
    8-A"`-style names untouched -- Slice C never rewrites them, and
    nothing anywhere resolves a canonical group by its name (only by
    `role == WHOLE_CLASS` and exact-one-class membership, matching
    `teaching_assignments_projection_service._whole_class_target`'s
    existing predicate exactly). Delete is rejected
    (`ClassSectionInUseError`, `409 CLASS_IN_USE`, `referenced_by`
    naming every referencing kind in the deterministic order
    `TEACHING_REQUIREMENT`/`RESERVED_BLOCK`/`SUBGROUP`/
    `MERGED_CLASSES`) whenever the *current* configuration still
    references the class or its canonical group; `SUBGROUP`/
    `MERGED_CLASSES` groups are never created, renamed, or deleted by
    Class CRUD -- only ever delete-blockers. A zero-or-duplicate
    canonical-group state is never silently repaired -- it surfaces as
    an internal defect (`class_section_rules.
    CanonicalWholeClassGroupInvariantError`, never a public
    application/HTTP outcome), matching this codebase's existing
    internal-defect discipline. Reuses `ClassSectionNotFoundError`/
    `ConfigurationLockedError` verbatim -- no second class-not-found
    exception, no Class-specific lock code. Zero schema/migration
    impact (Alembic stays at `cae76cba3c58`); zero frontend production
    changes. Test gate: 28 new pure `tests/test_class_section_service.py`
    cases (zero DB); 23 new `tests_web/test_class_section_api.py`
    HTTP-contract cases plus 11 new
    `tests_web/test_class_section_repository.py` cases (atomicity
    proofs for create/update/delete failure paths, plus a
    deterministic, sequential proof of both generation-vs-write race
    orderings for a Class mutation). Slice C's larger cumulative test
    count exposed a pre-existing `tests_web/conftest.py::live_db_engine`
    lifecycle defect (it `return`ed its `Engine` with no teardown hook,
    so server-side connections accumulated across a full-suite run
    until PostgreSQL's `max_connections` was reached); corrected as a
    narrow test-infrastructure fix (the fixture now `yield`s and
    disposes its `Engine` on every exit path), no production code
    touched -- the canonical single-process
    `uv run python -m pytest -q tests_web` now passes all 213 tests
    with zero DB-reachability skips, confirmed on two consecutive runs.
    Live-validated against a new,
    local-only, unlocked `class-crud-review-school`/
    `ay-class-crud-2026` dataset (retained, no `Schedule` generated) as
    well as the running app's real HTTP API -- create, read, rename
    (identity preserved, both display names updated atomically),
    duplicate rejection, unused-class delete (owned canonical group
    confirmed gone), referenced-class delete rejection
    (`CLASS_IN_USE` with multi-kind deterministic `referenced_by`), and
    case-sensitive same-*differently-cased*-name coexistence all
    confirmed; all three prior canonical/review datasets
    (`synthetic-school`/`ay-2026`, `synthetic-review-school`/
    `ay-review-2026`, `teacher-crud-review-school`/
    `ay-teacher-crud-2026`) reconfirmed unchanged throughout, including
    every pre-existing fixture canonical group's historical `"All of
    X"` name, verbatim. This entry records Slice C's implementation
    status only -- **the broader Real-School Setup MVP remains NOT
    complete.** Next slice per the approved setup contract: **Slice D
    -- Subjects/Activities CRUD** (not started, not designed in detail
    here).

---

**Technical correction (NOT an Owner Decision -- Owner Decision #38
remains unused) -- Teaching Assignment Activity-Kind Invariant (LOCKED;
IMPLEMENTED, REVIEWED, COMMITTED, and MERGED to `main` at commit
`bade46c` "fix: enforce ordinary teaching assignment activities" --
CLOSED. Not pushed.).** This is a
technical bug-fix record, not a new numbered Owner Decision: the
invariant it locks was already implied by existing `ActivityKind`
semantics (see Decision #33's neighboring `role` invariant style for
the same "already-implied-by-the-model, now-enforced" precedent) --
nothing here introduces new product scope requiring an owner's sign-off.
The Slice D design gate's own activity-kind consistency check found a
genuine, previously-untested defect (CASE B, confirmed by a
rollback-isolated real-PostgreSQL proof): `ActivityKind` already
documents `ORDINARY` as "scheduled via a `TeachingRequirement`" and
`CLUB` as "scheduled via a `ReservedBlock`" (`domain/activities.py`),
but neither direction was ever enforced -- `TeachingAssignmentService`
create/update only checked that `activity_id` *existed*, and
`TeachingAssignmentsProjectionService` exposed every activity,
including `CLUB`, as a selectable option. A real write against the
fixture's `club_chess` (`kind=CLUB`) succeeded and produced a genuine
`TeachingRequirement` before this correction.

**The locked invariant**: `TeachingRequirement.activity_id` must
reference `ActivityKind.ORDINARY`; `CLUB` is never a valid target.
`teaching_assignment_rules.py` gained one shared pure helper,
`require_ordinary_activity`, called from both `validate_create` and
`validate_update` in place of the old bare existence check -- exactly
the same function therefore runs in `TeachingAssignmentService`'s fast
un-locked precheck *and* inside
`SqlAlchemyTeachingAssignmentRepository`'s authoritative,
lock-protected recheck (the identical `validate` callback wiring
already established by Decision #36), with zero persistence-layer
changes required -- `persistence/teaching_assignment_repository.py` is
untouched, confirmed by `git diff`. A new error,
`NonOrdinaryActivityTargetError` (mirroring `NonWholeClassTargetError`'s
exact shape -- the activity genuinely exists, it is simply invalid for
this write surface, so this is never `UnknownReferenceError`), maps to
`422 {"code": "NON_ORDINARY_ACTIVITY_TARGET", "detail": "...",
"activity_id": "...", "actual_kind": "..."}`.
`TeachingAssignmentsProjectionService`'s `activities` option list now
filters to `ActivityKind.ORDINARY` only (one-line change, identical
response-item shape, zero frontend change -- confirmed,
`frontend/src/api/types.ts::ActivityOption` already carries no `kind`
field). `GET /config` remains deliberately unfiltered -- it is the
general configuration projection, not an editable selector, and
continues to expose both kinds with `kind` visible.

**Defense-in-depth**: `validation/preflight.py` gained
`_check_teaching_requirement_activity_kind`, reporting
`NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY` for any
`TeachingRequirement` targeting a non-`ORDINARY` activity in an
already-loaded `SchedulingProblem` -- catching a malformed
configuration reaching generation by any path other than the
now-guarded write service (legacy data, direct construction, future
import/admin tooling), never a replacement for the write-time check.
`ReservedBlock` was deliberately left untouched: no production
`ReservedBlock` write service/repository/route exists yet (confirmed by
exhaustive search), so there is no write surface to have a symmetrical
defect in -- widening scope there would be unjustified.

**Subject duplicate-name scope re-evaluated** (Slice D design
conclusion, not a new Owner Decision): the earlier Slice D gate's
"unique across all Activity kinds" recommendation was justified solely
by this now-corrected projection leak. With Teaching Assignments'
activity selector genuinely `ORDINARY`-only, `/config` being a general
kind-labeled dump rather than an unlabeled picker, and Club management
remaining a distinct future surface, Subject duplicate-name checking
should be scoped to `ActivityKind.ORDINARY` only (still exact,
case-sensitive, trimmed, matching Class's own rule) -- an ORDINARY
"Mathematics" and a CLUB "Mathematics" may coexist. Zero Owner Decision
required; this is a direct technical consequence of the correction, not
an independent product preference.

Zero schema/migration impact (Alembic stays at `cae76cba3c58`); zero
solver/`GenerateScheduleService` change (the existing frozen-dataclass
structural-equality reload-and-compare already covers
`problem.teaching_requirements`/`.activities`, unchanged by this
correction); zero frontend production change. Test gate: 2 new pure
`tests/test_teaching_assignment_service.py` cases (CLUB rejected on
create/update) plus 1 confirming ORDINARY still succeeds, 2 new
`tests/test_preflight.py` cases, 1 corrected + 1 new
`tests/test_teaching_assignments_projection_service.py` case, 4 new
`tests_web/test_teaching_assignment_api.py` cases (GET excludes CLUB,
POST/PUT reject CLUB with the exact 422 contract, `/config` still
exposes both kinds) -- core `tests -m "not slow"` 256 passed/5
deselected; canonical single-process `tests_web` 217 passed, zero
DB-reachability skips, confirmed on two consecutive runs; Teaching
Assignment regression (API + repository) 40/40; frontend 185/185, build
clean. Slice D Subject CRUD remains **NOT implemented** -- this
correction is scoped entirely to the pre-existing Teaching Assignment
defect.

---

**Slice D (Subjects / ORDINARY Activities CRUD) update: IMPLEMENTED,
REVIEWED, live API reviewed, COMMITTED, and MERGED to `main` at
commit `104bed6` "feat: add subject CRUD" -- CLOSED. Not pushed. Zero
new Owner Decisions required; Owner Decision #38 remains unused.**
"Subject" is not a new domain entity -- it is
the user-facing name for `Activity(kind=ORDINARY)`
(`domain/activities.py`), exactly as the pre-Slice-D correction's own
invariant already established; no Subject dataclass, no Subject table.
`GET/POST /schools/{school_id}/years/{year_id}/subjects` and
`PUT/DELETE .../subjects/{subject_id}`, backed by `SubjectService`/
`SqlAlchemyActivityRepository`, shaped exactly like `ClassSectionService`/
`SqlAlchemyClassSectionRepository`: a fast un-locked Decision #35 lock
precheck, then an authoritative, Decision #36-locked recheck against a
freshly-reloaded `SchedulingProblem` before committing, reusing
`persistence/configuration_write_lock.py` entirely unchanged. Natural
IDs are always server-generated as `activity_<uuid4().hex>` (never
`subject_<uuid>` -- the persisted/domain identity genuinely is an
`Activity`) via an injectable `activity_id_factory`; `kind` is never
accepted as client input and never mutated after create -- create
always persists `ORDINARY`, and update/delete only ever resolve an
existing `ORDINARY` target, both at the pure-rule level
(`subject_rules.find_ordinary_subject`) and authoritatively re-checked
against the raw ORM row inside `SqlAlchemyActivityRepository` itself
(`row.kind != "ORDINARY"` treated identically to a missing row).

**Filtered-resource-surface contract**: a `CLUB` activity ID passed to
`/subjects/{id}` is indistinguishable from a missing Subject --
`SubjectNotFoundError` (`404 {"detail": "Subject not found"}`) in
both cases, never a wrong-kind signal, never leaking `ActivityKind`.
`name` is trimmed with a blank result rejected (`422 INVALID_SUBJECT`,
diagnostic `BLANK_SUBJECT_NAME`); duplicate `Activity` names within
one `AcademicYear` are rejected (`409 DUPLICATE_SUBJECT`) by exact,
case-sensitive, trimmed comparison -- checked ONLY against other
`ORDINARY` activities, never against `CLUB` ones, so an identically-
named Subject and Club may coexist (this is the resolved conclusion
from the pre-Slice-D correction's own re-evaluation, not a new product
preference). Delete is rejected (`SubjectInUseError`, `409
SUBJECT_IN_USE`, `referenced_by` naming every referencing kind in the
deterministic order `TEACHING_REQUIREMENT`/`RESERVED_BLOCK` -- the
only two direct `activity_id` references in the persisted schema,
confirmed by exhaustive search; `FixedPlacement`/`ResourceRequirement`
are never direct references) whenever the *current* configuration
still references the subject; a direct `ReservedBlock` reference to an
`ORDINARY` activity still blocks deletion even though `ReservedBlock`
conceptually belongs to `CLUB` -- existing/imported data is never
silently reinterpreted. Activity ordinal is computed across BOTH
`ORDINARY` and `CLUB` rows (one shared ordering domain for the whole
`activity` table), never `ORDINARY` alone. `GET /subjects` never
exposes `kind` -- every member is already, by construction, a Subject.
`GET /config` remains fully unfiltered (both kinds, `kind` visible);
Teaching Assignments' activity options (already `ORDINARY`-only since
the pre-Slice-D correction) automatically include every new/renamed
Subject with zero sync step -- confirmed unmodified,
`teaching_assignments_projection_service.py` needed no change.

Zero schema/migration impact (Alembic stays at `cae76cba3c58`); zero
frontend production change; zero solver change. Test gate: 27 new pure
`tests/test_subject_service.py` cases (zero DB); 25 new
`tests_web/test_subject_api.py` HTTP-contract cases plus 12 new
`tests_web/test_subject_repository.py` cases (atomicity proofs for
create/update/delete failure paths including a CLUB-target repository-
level guard proof, plus a deterministic, sequential proof of both
generation-vs-write race orderings for a Subject mutation). Live-
validated against a new, local-only, unlocked
`subject-crud-review-school`/`ay-subject-crud-2026` dataset (retained,
no `Schedule` generated) as well as the running app's real HTTP API --
create, read, rename (identity preserved across `/subjects`, `/config`,
and Teaching Assignments), duplicate rejection, case-variant
coexistence, same-name-as-CLUB coexistence, unused-subject delete,
referenced-subject delete rejection, and CLUB-target
PUT/DELETE-through-`/subjects` both returning `404`/leaving the CLUB
row intact all confirmed; all four prior canonical/review datasets
(`synthetic-school`/`ay-2026`, `synthetic-review-school`/
`ay-review-2026`, `teacher-crud-review-school`/`ay-teacher-crud-2026`,
`class-crud-review-school`/`ay-class-crud-2026`) reconfirmed unchanged
throughout. Core `tests -m "not slow"` 283 passed/5 deselected;
canonical single-process `tests_web` 254 passed, zero DB-reachability
skips, confirmed on two consecutive runs; Teaching Assignment
regression (API + repository) 40/40 and the pre-Slice-D preflight
Activity-Kind tests 26/26, both reconfirmed unregressed; frontend
185/185, build clean. This entry records Slice D's implementation
status only -- **the broader Real-School Setup MVP remains NOT
complete.** Next slice per the approved setup contract: **Slice E --
School Setup frontend** (not started, not designed in detail here).

---

**Slice E (School Setup frontend) update: IMPLEMENTED, REVIEWED, a
local desktop browser sanity pass REVIEWED against the live pilot
dataset, COMMITTED, and MERGED to `main` at commit `8532193` "feat:
add school setup frontend" -- CLOSED. Not pushed. Zero new Owner
Decisions required; Owner Decision #38 remains unused.** The design
gate for this slice (`docs/DECISIONS.md`'s own
prior entry) approved two explicit deviations from its own sketched
recommendations, both ordinary implementation choices, not Owner
Decisions: (1) the existing flat top nav gains a third link, "School
Setup", ordered before "Teaching Assignments" -- no "Configuration"
dropdown/group container was introduced; the `/configuration/...` URL
prefix stays conceptual only; (2) create/edit/delete do not reuse
`AssignmentDrawer`'s slide-in dialog -- Teachers/Classes/Subjects use a
compact "+ Add ..." button that reveals a contained inline create
panel (Save/Cancel, collapsing on success), inline row-level edit
(the row itself switches into editable fields), and the same inline
confirm/cancel delete pattern `TeachingAssignmentsPage` already uses
(no `window.confirm()`, no new modal component).

One route, `/configuration/setup` -> `SchoolSetupPage`, with three
local tabs (Teachers/Classes/Subjects, no nested tab routes, no count
badges) implementing the WAI-ARIA "Tabs (Automatic Activation)"
pattern (`role="tablist"/"tab"/"tabpanel"`, roving `tabIndex`,
ArrowLeft/ArrowRight/Home/End both moving focus and activating).
Exactly one panel is ever mounted at a time -- switching tabs
unmounts the previous panel and mounts the next, which performs its
own independent GET; no cross-tab cache, no global store, no
requirement that all three resource projections load on initial page
view. Each panel (`TeachersPanel`/`ClassesPanel`/`SubjectsPanel`) is
fully self-contained, matching `TeachingAssignmentsPage`'s own
architecture: its own load/create/edit/delete state; its own GET
projection as sole source of truth, refetched (never hand-patched)
after every successful write; `SCHEDULING_CONFIGURATION_LOCKED`
lock-race handling identical to Teaching Assignments' own discipline
(refetch into the now-locked, read-only state rather than leaving a
stale form open). `configuration_locked` keeps every record visible
and disables (never hides) Add/Edit/Delete, with the existing
`.lock-banner` reused verbatim. `TEACHER_IN_USE`/`CLASS_IN_USE`/
`SUBJECT_IN_USE` `referenced_by` codes are mapped to the same
human-readable labels the design gate specified (Teaching assignments,
Teacher availability, Reserved activities, Subgroups, Merged classes),
with a safe raw-code fallback for forward compatibility;
`DUPLICATE_CLASS`/`DUPLICATE_SUBJECT` get a plain inline message;
Teachers intentionally has no duplicate-name rule, client-side or
otherwise (Slice B's own locked contract), proven by a passing test
that creates two identically-named teachers successfully. Neither the
canonical WHOLE_CLASS `ParticipantGroup` (Owner Decision #33) nor
`Activity.kind`/CLUB rows are exposed anywhere in the Classes/Subjects
panels -- confirmed both by code review and by dedicated tests
asserting the rendered page text never contains the internal id/role/
`ORDINARY`/`CLUB` strings. No backend response body was redesigned;
every request/response shape matches Slices B/C/D's already-CLOSED
contracts exactly. Zero schema/migration impact, zero solver change,
zero `TeachingAssignmentsPage`/`AssignmentDrawer` production change
(its own 55 tests reconfirmed passing unmodified).

Test gate: frontend `npm test` 259 passed (185 pre-existing + 74 new:
2 route/nav + 7 `teachers.ts` + 8 `classes.ts` + 9 `subjects.ts` API-
client tests + 14 `TeachersPanel` + 14 `ClassesPanel` + 14
`SubjectsPanel` + 9 `SchoolSetupPage` tests), `npm run build` clean
(`tsc --noEmit` + `vite build`); backend core `tests -m "not slow"`
283 passed/5 deselected (unchanged -- zero backend production code
touched); canonical single-process `tests_web` 254 passed (unchanged);
Alembic unchanged at `cae76cba3c58`, single head, no drift. A local
manual browser sanity pass was run (twice, once before closure and
once again during closure) against the real, running dev server
(`http://localhost:5173/configuration/setup`) pointed at the
canonical, locked `synthetic-school`/`ay-2026` pilot dataset --
read-only inspection only, no mutation attempted against it: page
hierarchy, tab bar, lock banner, and all three tabs' real data
(Teachers/Classes/Subjects, ORDINARY-only Subjects with no Club rows)
rendered correctly, and live ArrowRight keyboard activation was
confirmed to move focus and switch tabs correctly. The 640px
narrow-width stacked-row CSS was reviewed by code only -- a live
resized-browser screenshot was not reliably obtainable during this
slice's review; this is a non-blocking limitation, and full responsive
browser confirmation remains available to fold into Slice F if browser
tooling permits. This was a Slice E visual **sanity** check only, not
the Slice F real-school acceptance workflow -- Slice F (full
create/edit/delete/referenced-delete acceptance across a real browser
session) remains **NOT executed**. Explicitly not part of Slice E:
Clubs, Reserved Blocks, Teacher Availability, Subgroups, Merged
Classes, rooms/resources, calendar/day-period editing, School/
AcademicYear CRUD, users/auth, solver configuration, any change to
`TeachingAssignmentsPage`. The broader Real-School Setup MVP is
**not** complete -- this is Slice E only. Next slice per the approved
setup contract: **Slice F -- real-school browser acceptance / setup
smoke** (not started).

---

**Slice F (Real-school browser acceptance / setup smoke): EXECUTED,
PASSED, CLOSED.** Zero new Owner Decisions required; Owner Decision
#38 remains unused. This was a pure acceptance/QA slice against the
already-CLOSED Slices A-E -- zero production code (backend or
frontend) was changed, zero migrations were created, zero test files
were modified.

Dedicated acceptance dataset created and retained as durable local
evidence: `real-school-browser-smoke-school`/
`ay-real-school-browser-smoke-2026` -- a complete, generation-capable
clone of the canonical `synthetic-school`/`ay-2026` pilot configuration
(8 teachers, 4 classes, 8 ORDINARY + 2 CLUB activities, 25 teaching
requirements), built via the existing TEST-ONLY
`tests_web/support/problem_writer.py::write_scheduling_problem`
against a `SchedulingProblem` loaded from the pilot and remapped to
the new natural IDs -- created with zero `Schedule`/`ScheduleVersion`
rows so the acceptance workflow could start from a genuinely unlocked
state.

Using an isolated second Vite dev server (port 5174, shell-level
`VITE_SCHOOL_ID`/`VITE_ACADEMIC_YEAR_ID` env overrides, the normal
port-5173 canonical dev server and `frontend/.env.local` both left
untouched) against the real backend, the full real-browser acceptance
workflow was executed and observed passing at every step: School
Setup loaded unlocked with the correct nav order and default tab;
Teacher create/edit worked through the UI, and two identically-named
Teachers were both accepted and both cleanly deleted, proving the
no-duplicate-name contract live; Class create/edit worked, an exact
duplicate name was rejected with a clear inline message and left no
second row, and the canonical WHOLE_CLASS group/id was never rendered
(only ever confirmed via read-only DB inspection: e.g. the class
`Browser-QA-1A` and its canonical group `group_025367a7...` both
stayed entirely server-side); Subject create/edit worked identically,
an exact duplicate was rejected the same way, and the persisted
`Activity.kind` (`ORDINARY`) and natural ID (`activity_<uuid>`, never
`subject_<uuid>`) were only ever confirmed via read-only inspection,
never exposed in the UI; the newly-created Teacher/Class/Subject all
appeared automatically in the Teaching Assignments create drawer with
zero manual sync step, and no CLUB activity was ever offered there. A
real temporary Teaching Assignment was created through the browser
referencing all three, which then correctly blocked deletion of the
Teacher/Class/Subject with the exact same human-readable
`referenced_by` mapping documented in Slice E ("Teaching
assignments" -- never a raw `TEACHING_REQUIREMENT` string). The
temporary assignment was deleted through the browser, after which all
three temporary reference-data records deleted successfully, and the
smoke dataset's Teacher/Class/ORDINARY-Activity/CLUB-Activity/
TeachingRequirement counts were confirmed to return to their exact
pre-browser baseline (8/4/8/2/25) with zero leftover temporary
natural IDs or names, and `run_preflight` against the reloaded
`SchedulingProblem` returned zero errors both before and after this
whole round trip.

With the configuration restored to its complete baseline, a real
Schedule was generated through the browser's existing Timetable page
"Generate schedule" control (never the generation API called
directly) -- generation succeeded (`solver_status: OPTIMAL`, version
1), confirmed both by the rendered class timetable and by read-only
`Schedule`/`ScheduleVersion` inspection. School Setup was then
reloaded and confirmed locked exactly as designed on all three tabs:
every record stayed visible and readable (including the full
ORDINARY-only Subjects list, still zero Club rows), and every
Add/Edit/Delete control was visibly disabled with the `.lock-banner`
explanation -- no controls were bypassed. All five pre-existing
canonical/review datasets (`synthetic-school`/`ay-2026`,
`synthetic-review-school`/`ay-review-2026`,
`teacher-crud-review-school`/`ay-teacher-crud-2026`,
`class-crud-review-school`/`ay-class-crud-2026`,
`subject-crud-review-school`/`ay-subject-crud-2026`) were snapshotted
before and after the entire Slice F run, comparing Teacher/Class/
ORDINARY/CLUB/TeachingRequirement/Schedule counts -- the same recorded
counts held before and after for every one of the five, with no
change in any of them; this is unchanged according to the recorded
pre/post dataset snapshot fields, not a row-by-row or byte-level
database comparison, which was not performed.

One accurately-recorded limitation, non-blocking per the Slice E
precedent: a live narrow-viewport (640px) browser screenshot remained
unobtainable in this session's browser-automation tooling (window
resize did not affect the captured viewport); the 640px stacked-row
responsive CSS itself was not modified and remains code-reviewed only,
matching Slice E's own documented limitation. Desktop is the current
primary product target, so this does not block Slice F closure.

Regression baseline reconfirmed unchanged throughout: frontend `npm
test` 259 passed (one pre-existing, already-known timing-sensitive
flake in `TimetablePage.test.tsx` -- a file untouched by any slice --
was isolated and confirmed non-reproducible, both alone and across a
full clean rerun), `npm run build` clean; backend core `tests -m "not
slow"` 283 passed/5 deselected; canonical single-process `tests_web`
254 passed, zero DB-reachability skips; Alembic unchanged at
`cae76cba3c58`, single head, no drift. `git status`/`git diff` were
empty at every gate throughout Slice F -- the entire acceptance
workflow changed only local PostgreSQL state (the new smoke dataset),
never a single tracked file.

**Slices A through F are now CLOSED. The scoped Real-School Setup MVP
is COMPLETE**: the product now supports, end-to-end, through the real
browser -- School Setup (Teachers, Classes, Subjects) -> Teaching
Assignments -> Schedule Generation -> Timetable/read-only
configuration-lock workflow. This explicitly does **not** mean the
broader SchoolTimetable product is finished: Teacher Availability
editing, Clubs/Reserved Blocks editing, Subgroups/Merged Classes
editing, rooms/resources administration, a calendar/day-period editor,
School/AcademicYear CRUD, authentication/user management, and other
advanced scheduling-policy UI all remain explicitly outside this MVP's
scope, as future work.

---

**Owner Decision #38 -- Teacher Availability exposes all three
established `AvailabilityStatus` states.** The domain/solver already
implemented, before this decision, all three states the design gate
discovered: `AVAILABLE` (the sparse default -- absence of an explicit
`TeacherAvailability` row; schedulable normally), `PREFER_NOT` (a
persisted sparse exception; a SOFT solver preference -- discouraged
via a weighted objective penalty, never forbidden), and `UNAVAILABLE`
(a persisted sparse exception; a HARD solver constraint -- the
corresponding lesson variable is forced to zero). The first Teacher
Availability product/API surface exposes **all three** -- `PREFER_NOT`
is not deferred as a future feature, and this is not a binary-only
surface. This is the one genuine Owner Decision the design gate
identified (the alternative -- launching binary `AVAILABLE`/
`UNAVAILABLE`-only and deferring `PREFER_NOT` UI exposure to a later
slice -- was explicitly rejected in favor of exposing the
already-fully-implemented three-state model from the start).

**Real-School Setup MVP's next product phase -- Teacher Availability
Slice A (backend read/bulk-write/persistence/API) update: IMPLEMENTED,
REVIEWED, real HTTP API reviewed, COMMITTED, and MERGED to `main` at
commit `24c1b04` "feat: add teacher availability backend" -- CLOSED.
Not pushed.** Backend-only, per Owner Decision #38's locked write
contract: the write unit is one Teacher's *complete desired sparse
exception set*, never per-cell CRUD -- `PUT
/schools/{school_id}/years/{year_id}/teacher-availability/{teacher_id}`
atomically reconciles that Teacher's persisted `PREFER_NOT`/
`UNAVAILABLE` rows to match the request exactly (existing cells no
longer desired are deleted, a changed status is updated in place
preserving `ordinal`, newly-desired cells are inserted with the next
`ordinal` computed across the whole `AcademicYear` -- never restarting
per Teacher -- sorted by `Day.index`/`Period.index` for deterministic
insertion order, never request order); an explicit `AVAILABLE` entry
in the request is rejected (`422 INVALID_TEACHER_AVAILABILITY`,
`AVAILABLE_EXCEPTION_MUST_BE_OMITTED`), never silently normalized
away, keeping the sparse contract unambiguous; an in-request duplicate
`(day_id, period_id)` cell is rejected
(`DUPLICATE_AVAILABILITY_CELL`), never last-write-wins; an unknown
status string is rejected (`UNKNOWN_AVAILABILITY_STATUS`); an unknown
`day_id`/`period_id` reuses the existing, genuinely generic
`UnknownReferenceError`/`422 UNKNOWN_REFERENCE` contract verbatim (not
forced -- its wording carries no Teaching-Assignment-specific
language); a missing Teacher reuses the existing `TeacherNotFoundError`/
`404 "Teacher not found"` contract verbatim. `GET
/schools/{school_id}/years/{year_id}/teacher-availability` is a new,
dedicated, sparse *exception-only* projection (`configuration_locked`,
authoritatively-ordered `teachers`/`days`/`periods`, and only
`PREFER_NOT`/`UNAVAILABLE` rows -- never `AVAILABLE`) -- entirely
separate from, and non-breaking to, `GET /config`'s own existing
`teacher_availabilities` field, which continues exposing every
persisted row (including any legacy explicit `AVAILABLE` row) under
its own unrelated general-configuration contract, unchanged.
`TeacherAvailabilityRepository` (the fifth configuration write port)
reuses `persistence/configuration_write_lock.py` unchanged -- the
identical `SCHEDULING_CONFIGURATION_LOCKED` (Decision #35) and
generation-race (Decision #36) guarantees every other configuration
writer already has, proven by two new deterministic race tests: (A)
an availability write commits after generation loads its problem but
before generation's final lock-protected persist, which then detects
the changed `SchedulingProblem` (`teacher_availabilities` is an
ordinary dataclass field, already covered by the frozen dataclass's
auto-generated `__eq__` with zero new code) and aborts with
`ConfigurationChangedDuringGenerationError`, persisting no `Schedule`
row; (B) generation persists first, and a subsequent availability
write is rejected with the standard `SCHEDULING_CONFIGURATION_LOCKED`
contract, with zero row changes. A narrow preflight defense-in-depth
diagnostic, `DUPLICATE_TEACHER_AVAILABILITY_CELL`, was also added --
persistence itself cannot contain two rows for the same
`(teacher_id, day_id, period_id)` cell (the table's own composite
primary key forbids it), but an arbitrary in-memory/imported
`SchedulingProblem` could, and `ProblemIndex.get_availability` would
otherwise silently let the last one win; preflight now rejects such a
problem outright, for either matching or conflicting duplicate
statuses. Existing Teacher-delete-blocked-by-`TEACHER_AVAILABILITY`
behavior is unchanged and reconfirmed unregressed -- this slice never
touches `TeacherService`/`TeacherRepository`. Zero schema/migration
impact (the `teacher_availability` table's composite natural primary
key, `ordinal` column with its own `AcademicYear`-scoped uniqueness
constraint, `status` check constraint, and FK/cascade/restrict
behavior were already fully sufficient for a production write path --
confirmed by inspection during the design gate, not merely assumed),
zero frontend production change (confirmed: `git diff --name-only --
frontend/` empty), zero solver production change (the existing
`UNAVAILABLE`-forces-zero and `PREFER_NOT`-weighted-penalty code is
untouched; only new tests were added proving both halves of the
distinction against small, deterministic, purpose-built problems,
since the design gate could previously only prove them by code
construction).

A NEW local-only, unlocked review dataset,
`teacher-availability-review-school`/
`ay-teacher-availability-review-2026` (a full clone of the canonical
pilot configuration, seeded via the existing TEST-ONLY
`write_scheduling_problem`, zero `Schedule`), was created and is
retained as durable local acceptance evidence -- live-validated this
session against the real, running dev server's actual HTTP API (not
merely the application-service layer): `GET .../teacher-availability`
returns the fixture's existing 3 sparse exception rows unchanged;
`PUT .../teacher-availability/t_math` with one `UNAVAILABLE` + one
`PREFER_NOT` cell succeeds and is reflected exactly by both the
dedicated `GET` and `GET /config`; `PUT` with an empty exception list
clears only `t_math`'s exceptions, leaving `t_science`/`t_history`'s
pre-existing rows untouched; no `Schedule` exists throughout. All six
pre-existing canonical/review datasets (`synthetic-school`/`ay-2026`,
`synthetic-review-school`/`ay-review-2026`,
`teacher-crud-review-school`/`ay-teacher-crud-2026`,
`class-crud-review-school`/`ay-class-crud-2026`,
`subject-crud-review-school`/`ay-subject-crud-2026`,
`real-school-browser-smoke-school`/`ay-real-school-browser-smoke-2026`)
were snapshotted (now including `TeacherAvailability` row counts) and
confirmed unchanged -- none were touched; this implementation issued
no write against any of them, and every automated test runs against
the separate test database, never the dev database these datasets
live in.

Test gate: core `tests -m "not slow"` 309 passed/5 deselected (283
pre-existing + 26 new: 20 pure `tests/test_teacher_availability_service.py`
+ 3 `tests/test_preflight.py` duplicate-cell diagnostics + 3
`tests/test_teacher_availability_solver.py` HARD/SOFT proofs);
canonical single-process `tests_web` 288 passed (254 pre-existing + 34
new: 14 `tests_web/test_teacher_availability_repository.py` + 20
`tests_web/test_teacher_availability_api.py`), zero DB-reachability
skips, confirmed on two consecutive runs; existing Teacher-delete-
blocker, `/config` serializer, persistence mapper/schema, and
`UNAVAILABLE`-HARD solver tests all reconfirmed unregressed; frontend
185+74=259 passed (unchanged), build clean; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Explicitly NOT part of this
slice: any frontend Teacher Availability surface (page, nav, route,
API client, types, matrix UI, CSS), a per-cell write endpoint,
immediate-save behavior, Club/ReservedBlock UI, a calendar editor, any
Teacher CRUD change, and any migration. The broader Real-School Setup
MVP successor work is **not** complete -- this is Teacher Availability
Slice A only, and **Availability B (frontend page/grid) has not been
implemented.** The overall Teacher Availability phase is **not**
complete. Next planned slice: **Teacher Availability Slice B --
frontend page/grid** (not started).

**[Historical -- superseded by the closure entry below.] Teacher
Availability Slice B (frontend page/3-state matrix) -- implemented
and PENDING TECHNICAL/VISUAL REVIEW. Not committed, not merged, not
pushed.** Working tree only, on `feature/teacher-availability-frontend`,
left intentionally uncommitted for review per the owner's explicit
process instruction. This entry records what exists on that branch;
it does not close Availability B and does not mark the overall
Teacher Availability phase complete. **Owner Decision #39 was NOT
created in this slice** -- Owner Decision #38 (all three
`AVAILABLE`/`PREFER_NOT`/`UNAVAILABLE` states exposed to the user)
remains the only governing decision, unchanged.

Consumes the Slice A backend contract exactly as merged, with zero
backend/persistence/solver/migration change: `GET
.../teacher-availability` (`configuration_locked`, `teachers`,
`days`, `periods` with `is_instructional`, sparse `exceptions` that
never contain `AVAILABLE`) and `PUT
.../teacher-availability/{teacher_id}` (whole-Teacher sparse exception
replacement, never per-cell CRUD), with the same `404`/`422
UNKNOWN_REFERENCE`/`422 INVALID_TEACHER_AVAILABILITY`/`409
SCHEDULING_CONFIGURATION_LOCKED` error contract. New frontend-only
surface: hand-mirrored wire/UI types in `api/types.ts`; a thin
`api/teacherAvailability.ts` client (`getTeacherAvailability`,
`replaceTeacherAvailability`); a new `/configuration/teacher-
availability` route and its fourth flat nav link (order: Timetable,
School Setup, Teacher Availability, Teaching Assignments);
`TeacherAvailabilityPage.tsx` owning all page/draft/dirty state; two
narrowly-scoped presentational components, `AvailabilityGrid.tsx`
(Period rows x Day columns on desktop, matching `TimetableGrid`'s own
orientation, plus an unconditionally-rendered per-Day-stacked mobile
markup toggled by a single 640px CSS media query, no JS viewport
listener) and `AvailabilityLegend.tsx` (always-visible, never
tooltip-only). Every cell is one real `<button>` cycling
AVAILABLE -> PREFER_NOT -> UNAVAILABLE -> AVAILABLE; deliberately no
`aria-pressed`/`aria-checked` (both are binary/mixed-only and cannot
represent three independent states) -- a dynamic `aria-label` states
the Day, Period, current status, and next status instead, plus a
single shared `aria-live="polite"` region for redundant confirmation.
Dirty state is derived (normalized draft-vs-server comparison), never
a manually-toggled boolean; Save issues one whole-Teacher PUT
followed by an authoritative GET refetch and draft rebuild (never
optimistic), preserving the draft and showing an action-level error
on failure; Reset is client-only (no API call); a `409
SCHEDULING_CONFIGURATION_LOCKED` race during Save discards the stale
draft, refetches, and leaves the page read-only. Bulk actions
("Clear all", "Set all available", etc.) are explicitly out of scope
for this slice, as directed.

Frontend test gate: new/modified tests 40 (10 `App.test.tsx`,
including the updated nav-order assertion and two new route/nav
tests; 9 new `api/teacherAvailability.test.ts`; 29 new
`pages/TeacherAvailabilityPage.test.tsx`), full suite 259 (prior
baseline) + 40 = **299 passed**, zero skips, confirmed clean across
six consecutive full 18-file-suite runs after a real test-file race
was found and fixed (a "wait for the page heading" helper matched
both the loading and the ready state, since both render an identical
`<h1>`; replaced with ready-state-specific waits throughout); existing
School Setup/Teaching Assignments/Timetable/App-routing suites (120
tests) reconfirmed unregressed; `npm run build` clean, `dist/`
removed. Backend regression reconfirmed unchanged though untouched by
this slice: core `tests -m "not slow"` 309 passed/5 deselected;
`tests_web` 288 passed, zero skips; Alembic unchanged at
`cae76cba3c58`, single head, no drift. Scope audit confirmed changes
confined to `frontend/src/` plus this docs update -- zero backend,
zero migration, zero `package.json`/`package-lock.json`/dependency
change.

Explicitly NOT part of this slice and NOT executed: Availability C
(no schedule generation, no solver acceptance run against this
surface), Owner Decision #39, any bulk-edit action, any Teacher CRUD
change, any backend/persistence/solver/migration edit. The overall
Teacher Availability phase is **still not complete** -- this entry
records an implemented-but-unreviewed candidate, pending the owner's
technical and visual review before any commit, merge, or further
slice.

**[Historical -- superseded by the closure entry below.] Teacher
Availability Slice B -- review-blocking correctness defect found
and corrected. STILL PENDING REVIEW; still not committed, merged, or
pushed.** A stale-authority safety gap was found in
`TeacherAvailabilityPage.tsx`: after a successful PUT, the mandatory
authoritative GET refetch could itself fail, leaving `refreshState`
`stale` -- but Save/Reset/the Teacher selector were gated only on
`isDirty`/`isSaving`/`configuration_locked`, never on
`refreshState.status`, so they could remain actionable (and Reset
could restore the draft from the now-superseded pre-write projection)
while the server had already diverged from the client. The same gap
existed on the `409 SCHEDULING_CONFIGURATION_LOCKED` lock-race path
when its own post-race refetch failed. Fixed by applying the page's
existing `controlsDisabled` derivation (`refreshState.status !==
"idle" || isSaving` -- the same name and shape as
`TeachingAssignmentsPage`'s own precedent) uniformly to the grid,
Save, and Reset, and to the Teacher selector (without the `locked`
term, since a locked-but-authoritative, non-stale projection must
still permit Teacher browsing -- "locked" and "stale" are distinct:
locked is read-only-but-authoritative, stale is authority-unknown).
`handleSave`/`handleReset`/`handleTeacherChange` also each gained a
defensive `refreshState.status !== "idle"` guard in the handler body
itself, not just the button's `disabled` attribute. Three new tests
were added under a `describe` block reusing
`TeachingAssignmentsPage.test.tsx`'s own exact stale-projection-safety
naming: a successful-Save/failed-refresh round trip (old data stays
visible, the PUT itself is never reported as failed, every mutation
control disables, a click on a disabled control cannot cause a second
PUT, Retry recovers and re-derives a clean dirty state), a lock-race
whose own post-race refresh also fails (same full disabling, Retry
then loads the authoritative locked projection with the Teacher
selector re-enabled but Save/Reset/cells still disabled because of
`locked`), and a 404-Teacher-not-found write whose refetch also fails
(confirming the shared stale-handling path, with no bespoke second
state machine needed). `TeachingAssignmentsPage.tsx`/`.test.tsx` were
not touched; its own three stale-projection-safety tests were rerun
unmodified and still pass, confirming Availability B now follows the
identical principle without regressing the precedent it was copied
from.

Corrected, precise test accounting (the prior entry's "10 + 9 + 29 =
40" phrasing added a modified file's post-change total to two new
files' totals, which is not a valid net-new count): `App.test.tsx`
carries 8 tests on `main` and 10 now, a net-new delta of **2**;
`api/teacherAvailability.test.ts` is a new file with 9 tests, net-new
**9**; `pages/TeacherAvailabilityPage.test.tsx` is a new file with
**32** tests (29 from the original slice plus the 3 stale-authority
tests added by this correction), net-new **32**. Actual net-new
relative to the 259-test `main` baseline: 2 + 9 + 32 = **43**. Full
suite: 259 + 43 = **302 passed**, zero skips, matching three
consecutive full 18-file-suite runs exactly (`302`/`302`/`302`);
`npm run build` clean, `dist/` removed. Existing School
Setup/Teaching Assignments/Timetable/App-routing suites reconfirmed
unregressed. Backend untouched: `git diff --name-only -- src/
school_timetable tests tests_web alembic` empty; Alembic unchanged at
`cae76cba3c58`, single head, no drift. An incidental untracked
repo-root `uv.lock`, a byproduct of running `uv run` in a prior
session, was removed (`rm -f uv.lock`); it was never staged and no
dependency changed. Scope confined to the existing Availability B
frontend files plus this docs update -- zero
`src/school_timetable`/`tests`/`tests_web`/`alembic`/`package.json`/
`package-lock.json`/`uv.lock` change. Owner Decision #39 was **not**
created; Availability C was **not** executed. The overall Teacher
Availability phase remains **not complete** -- this correction does
not close Availability B; it remains an implemented, now
defect-corrected, still-uncommitted candidate on
`feature/teacher-availability-frontend`, pending the owner's review.

**Teacher Availability -- Availability B (frontend page / 3-state weekly
matrix): IMPLEMENTED, REVIEWED, technical re-review PASSED, read-only
visual review PASSED, COMMITTED, MERGED to `main`, CLOSED.**
Implementation commit `ca464e26a7b365d040467176ecabd97716ee604d`
("feat: add teacher availability frontend"), fast-forward merged to
`main` from `feature/teacher-availability-frontend` (now deleted).
This entry is the authoritative current-state record; the two
preceding historical entries above capture the review process that
led here (initial implementation, then the stale-authority defect
found and corrected) and are superseded by it.

Frontend surface: route `/configuration/teacher-availability`; nav
order Timetable, School Setup, Teacher Availability, Teaching
Assignments. Desktop editor: Period rows x Day columns, instructional
Periods only, matching `TimetableGrid`'s own orientation. Mobile:
per-Day stacked sections at <=640px, a single CSS media query, no
viewport JS -- both render paths share the same draft/state
callbacks, and the hidden branch is `display: none` (not reachable by
keyboard/screen reader). State model: `Available` -> `Prefer not` ->
`Unavailable` -> `Available`, one real `<button type="button">` per
cell, no `aria-pressed`/`aria-checked`, a dynamic `aria-label`
stating Day/Period/current status/next action, visible symbol + text
(never color alone), one shared `aria-live="polite"` region. Draft is
the sparse selected-Teacher exception set; `AVAILABLE` is represented
by a cell's absence and never enters the wire request/response.
Save: whole-Teacher PUT -> mandatory authoritative GET refetch ->
draft rebuild, never optimistic. Reset: client-only, no API call. No
bulk action. Dirty state is derived from a normalized draft-vs-
authoritative comparison; the Teacher selector is disabled while
dirty.

Stale-authority safety (the corrected defect, now covered by
dedicated regression tests): after a successful Save whose
authoritative GET refetch fails, old/stale data may remain visible,
but every mutation entry point -- cells, Save, Reset, and the Teacher
selector -- disables, with Retry as the only recovery path; the same
applies after a `409 SCHEDULING_CONFIGURATION_LOCKED` race whose own
post-race refetch fails; and after a `404` Teacher-not-found write
whose refetch fails, via the same shared handling (no separate state
machine). Once an authoritative (non-stale) locked projection loads,
the distinction holds: the Teacher selector re-enables and data stays
readable, while cells/Save/Reset remain disabled because the
configuration itself is locked.

Precise frontend test accounting: `main`-before-slice baseline 259;
net-new 43 (`App.test.tsx` +2, `api/teacherAvailability.test.ts` +9
new file, `pages/TeacherAvailabilityPage.test.tsx` +32 new file);
final total **302**, 18 test files, zero skips, confirmed identical
across three consecutive full-suite runs (302/302/302) both before
this merge and reconfirmed after it on `main`. Build clean. Backend:
zero diff under `src/school_timetable`/`tests`/`tests_web`/`alembic`;
Alembic unchanged at `cae76cba3c58`, single head, no drift; zero
migration; zero dependency change; no `uv.lock` present or tracked.

Owner Decision #38 remains **LOCKED**. Owner Decision #39 **does not
exist** -- none of this slice's stale-authority/locked/disabled-
control behavior required a new owner decision; it is an
implementation-level safety property of the already-locked #38
contract. **Availability C (real-browser write/persistence + solver
acceptance against this surface) was NOT executed** and remains the
next planned slice. **The overall Teacher Availability phase is NOT
complete** -- Availability B's closure is scoped to the frontend page
only. Nothing was pushed to any remote.

**Teacher Availability -- Availability C (real-browser write /
persistence / solver / lock acceptance): EXECUTED, PASSED, CLOSED.**
Pure acceptance, not feature implementation -- zero production
source diff (frontend, backend, and migrations all unchanged;
confirmed via `git status --short`/`git diff --name-only` returning
empty both before and after). Two NEW, local-only, dedicated
SchedulingProblem datasets were created via the existing TEST-ONLY
`tests_web/support/problem_writer.py` writer (the same tool used to
seed the Slice A review dataset), derived from the small deterministic
two-teacher (`t_target` + an unconstrained `t_filler`) minimal-problem
pattern already used by `tests/test_teacher_availability_solver.py`,
rather than cloning the full 40-period pilot fixture -- both pass
`run_preflight` cleanly and started with zero `Schedule`.

**Choice dataset** (`teacher-availability-browser-choice-school` /
`ay-teacher-availability-browser-choice-2026`): one day, three
instructional periods (A=`p1`, B=`p2`, C=`p3`), `t_target`'s one-
period lesson plus `t_filler`'s two-period filler lesson exactly
filling the three-slot class occupancy, zero availability exceptions
at creation. A temporary isolated frontend instance (port 5199, real
backend, `frontend/.env.local` untouched) was used to: load the page
(auto-selected Teacher, Period rows x Day columns, all three cells
"Available"); cycle B to `Prefer not` and C to `Unavailable` through
the real cell buttons (dirty state, Teacher-selector disable, and
Save/Reset enable all observed live); click the real Save button
(one real PUT, followed by the page's own authoritative GET, dirty
clearing, controls re-enabling). Read-only PostgreSQL inspection
confirmed exactly two persisted rows (`t_target`/`p2`/`PREFER_NOT`,
`t_target`/`p3`/`UNAVAILABLE`) and zero row for A (the sparse default);
a real full-page reload reproduced all three states unchanged; the
raw `GET .../config` `teacher_availabilities` array carried both
rows unmodified. Schedule generation was run through the existing
"Generate schedule" button on `/timetable` (never a direct
`ScheduleRepository`/generation-API call) and succeeded
(`solver_status=OPTIMAL`, version 1, `total_soft_penalty=0`). Direct
read-only inspection of every `ScheduleEntry` for the academic year
showed `t_target`'s only entry at `(mon, p1)` -- A -- and zero entries
at B or C; `t_filler` occupied both B and C. This single dataset
proves both halves of Owner Decision #38's HARD/SOFT contract
end-to-end through the real product surface: `UNAVAILABLE` (C) was
never used (HARD), and the equivalent `AVAILABLE` alternative (A) was
chosen over the merely-discouraged `PREFER_NOT` slot (B) at zero
soft-objective cost (SOFT, genuinely avoided rather than forbidden).
Returning to `/configuration/teacher-availability` post-generation
showed the lock banner, both persisted states still readable, every
cell/Save/Reset control disabled, and the Teacher selector still
usable (switching to `t_filler` and back worked; a click on a
disabled cell caused no state change). As supporting evidence, a
direct API `PUT` against the now-locked dataset (bypassing the UI)
returned `409 SCHEDULING_CONFIGURATION_LOCKED` and left both
persisted rows unchanged.

**Soft-required dataset**
(`teacher-availability-browser-required-school` /
`ay-teacher-availability-browser-required-2026`): one day, two
instructional periods (A=`p1`, B=`p2`); B was pre-seeded (at dataset
creation, not through the browser) as `UNAVAILABLE` for `t_target`,
eliminating it as a HARD constraint so A is the *only* feasible slot
before any browser write. Through the same temporary-instance pattern
(port 5200), the real cell button at A was cycled to `Prefer not` and
saved (one real PUT, authoritative refetch, persisted, reload-
confirmed: `t_target`/`p1`/`PREFER_NOT`). Schedule generation via the
same real "Generate schedule" control succeeded
(`solver_status=OPTIMAL`, version 1, `total_soft_penalty=5` -- a
nonzero penalty, showing the solver genuinely paid the SOFT cost
rather than the slot being silently infeasible or the preference
being ignored); `t_target`'s only `ScheduleEntry` was at `(mon, p1)`
-- A, the `PREFER_NOT` slot. This proves `PREFER_NOT` remains a true
SOFT preference: it never blocks a placement, even when it is the
only option. Post-generation, the page independently reconfirmed the
same lock contract (readable `Prefer not`, disabled cells/Save/Reset,
usable Teacher selector) on this second, unrelated dataset.

Responsive sanity: the same known browser-automation limitation from
Slices B/E/F recurred (a `resize_window` call does not propagate to
the actual rendered viewport in this session's tooling --
`window.innerWidth` stayed `1346` and `matchMedia("(max-width:
640px)")` stayed `false` after a 400x700 resize request) -- not
fabricated; reported honestly. Structurally reconfirmed instead: both
DOM branches render simultaneously and the exact `@media (max-width:
640px)` rule (unchanged, verified present) is the only thing that
would toggle them, matching Availability B's own already-green
automated responsive test coverage -- non-blocking per the same
precedent.

Pre-existing canonical/review datasets (`synthetic-school`,
`synthetic-review-school`, `teacher-crud-review-school`,
`class-crud-review-school`, `subject-crud-review-school`,
`real-school-browser-smoke-school`,
`teacher-availability-review-school`) were snapshotted before and
after this entire acceptance run across the same recorded fields
(Teacher/Class/ORDINARY/CLUB/TeachingRequirement/TeacherAvailability/
Schedule counts) -- the same recorded snapshot/count fields held
identical before and after for all seven; this was never claimed nor
performed as a byte-for-byte or row-by-row comparison. The two new
Availability C datasets are intentionally excluded from that equality
claim (they were created and mutated on purpose) and are retained,
never cleaned up, as durable local acceptance/review evidence for
future debugging -- both now carry a persisted `Schedule` and are
locked, exactly like `teacher-availability-review-school` before them.

Final regression, reconfirmed identical to the pre-acceptance
baseline: frontend `npm test` 302 passed/18 files/zero skips
(confirmed clean across multiple full-suite runs both before and
after browser acceptance -- one isolated single-test flake was
observed and reconfirmed non-reproducible on immediate rerun, matching
the project's known environmental full-suite-contention pattern, not
a regression), `npm run build` clean, `dist/` removed; backend core
`tests -m "not slow"` 309 passed/5 deselected; canonical single-
process `tests_web` 288 passed, zero skips; Alembic unchanged at
`cae76cba3c58`, single head, no drift. **Zero production source
changes** (frontend and backend both), **zero test-source changes**,
**zero migration**, **zero dependency change** -- this slice is pure
acceptance against the already-closed Availability A/B surfaces.
**Owner Decision #39 was not created.**

**The Teacher Availability phase is now COMPLETE and CLOSED**:
Availability A (backend read/bulk-write/persistence/API) CLOSED,
Availability B (frontend 3-state matrix) CLOSED, Availability C
(real-browser write/persistence/solver/lock acceptance) CLOSED. Owner
Decision #38 remains the sole, authoritative, locked decision
governing this feature -- no #39 was ever needed. This closure covers
Teacher Availability specifically and explicitly does **not** mean
Teacher workload policies, gap minimization UI, Clubs/Reserved Blocks,
rooms/resources, subgroups/merged classes, or a calendar editor are
complete -- none of those were touched. **Next planned product phase:
Clubs / Reserved Blocks.**

**[Historical -- superseded by the closure entry below.] Reserved
Activities -- Reserved A1 (Special Activity catalog backend):
IMPLEMENTED on `feature/special-activity-backend`. Technical review
PASSED, real HTTP API review PASSED, automated gates PASSED. NOT yet
committed, NOT merged, NOT pushed.** User-facing product
terminology corrected per the design-gate review: **Special Activity**
= `Activity(kind=CLUB)`, the catalog/reference-data half of the phase;
**Reserved Activity** (a `ReservedBlock`) is a separate concept
belonging to Reserved A2, not implemented here. The internal domain
enum/persistence value `CLUB` is unchanged and never renamed; it is
simply never exposed raw through this API surface, mirroring
"Subject"'s own relationship to `Activity(kind=ORDINARY)` exactly.

Reuses the existing `Activity` domain object and `activity` ORM table
verbatim -- **zero schema change, zero migration, no new domain
entity, no new enum.** CRUD contract: `name` is the sole user-managed
field; `kind` is always `CLUB` server-side, never client-controlled.
Duplicate rule: exact, case-sensitive, trimmed name rejected only
among existing `CLUB` activities in the same `AcademicYear`; an
identically-named `ORDINARY` Subject is never a conflict (and vice
versa) -- the exact symmetric mirror of `subject_rules.find_duplicate`.
Natural ID: server-generated `activity_<uuid4().hex>`, identical
scheme to Subject. Ordinal: the single `AcademicYear`-wide `Activity`
ordinal sequence shared across both `ORDINARY` and `CLUB` (max across
the whole table + 1) -- never renumbered, never computed among CLUB
alone. Delete: blocked only by a genuine `ReservedBlock` reference
(`SPECIAL_ACTIVITY_IN_USE`, `referenced_by: ["RESERVED_BLOCK"]`),
identical mechanism to Subject's own `RESERVED_BLOCK` blocker
(`subject_rules.py`'s existing behavior is completely untouched).
Kind isolation: an `ORDINARY` activity ID passed to any Special
Activity endpoint behaves as `SpecialActivityNotFoundError` (404
`"Special activity not found"`) -- never a wrong-kind conflict, never
mutated, matching `SubjectService`'s own filtered-resource-surface
contract precisely (proven by dedicated repository-level tests that
target `math` directly against this new write port).

**Mandatory derived-name synchronization invariant** (the corrected
product contract's own consequence, not Owner Decision #39):
`ReservedBlock.name` is server-derived from its Activity's `name`,
never independent user input. Renaming a Special Activity therefore
atomically updates, in the SAME transaction as the Activity rename,
every persisted `ReservedBlock.name` whose `activity_id` references
it -- proven for a single referencing block, for multiple blocks
referencing the same renamed Activity (all synchronized), for an
unrelated block referencing a different Activity (left untouched),
and for a failed rename (zero partial synchronization -- neither the
Activity name nor any `ReservedBlock.name` changes). Timetable
projection behavior is completely unchanged: Class/Teacher Timetable
continue displaying `Activity.name`, never `ReservedBlock.name`,
exactly as before.

Configuration lock: reuses `persistence/configuration_write_lock.py`
verbatim, unchanged -- the same `resolve_year_id`/`lock_academic_year`/
`reject_if_configuration_locked` sequence every other configuration
writer already uses. All three mutations (`POST`/`PUT`/`DELETE`)
reject with `409 SCHEDULING_CONFIGURATION_LOCKED` once any `Schedule`
exists; `GET` remains readable regardless. Generation-race safety
(Owner Decision #36): unchanged production infrastructure --
`SchedulingProblem`'s frozen-dataclass equality already includes
`activities` and `reserved_blocks` as ordinary fields, so both a
Special Activity create/rename/delete AND its dependent
`ReservedBlock.name` synchronization are automatically detected by the
existing reload-and-compare check in `persist_initial_version`; proven
with two new deterministic race tests mirroring the Subject-write
precedent exactly (generation loads stale problem -> Special Activity
rename commits (including block-name sync) -> generation's final
persist raises `ConfigurationChangedDuringGenerationError`, zero
Schedule persisted; and the reverse ordering, where generation commits
first and a subsequent rename attempt is rejected under Decision #35
with the configuration, including every `ReservedBlock.name`, left
unchanged).

Repository boundary: a new, separate `SpecialActivityRepository` port
and `SqlAlchemySpecialActivityRepository` adapter -- deliberately NOT
an extension of the existing `ActivityRepository`, whose own docstring
already commits to never exposing Club management; that guarantee is
preserved by adding a sibling port instead, reusing the same
lock/recheck/validate discipline and the same shared
`configuration_write_lock.py` primitives. `SpecialActivityService`
mirrors `SubjectService`'s exact shape and orchestration discipline.

New routes: `GET/POST /schools/{school_id}/years/{year_id}/
special-activities`, `PUT/DELETE .../special-activities/
{special_activity_id}` -- error mapping mirrors `subject_routes.py`
exactly (422 `INVALID_SPECIAL_ACTIVITY`, 409
`DUPLICATE_SPECIAL_ACTIVITY`/`SPECIAL_ACTIVITY_IN_USE`/
`SCHEDULING_CONFIGURATION_LOCKED`, 404 `"Special activity not found"`/
`"Scheduling configuration not found"`). `/config`'s own contract is
completely unchanged -- it continues exposing every `Activity` with
its raw `kind`, now correctly reflecting a rename's synchronized
`ReservedBlock.name` values immediately, proven end-to-end via a
dedicated API test and via the real running backend against a new
local-only review dataset.

Test gate: 26 new pure `tests/test_special_activity_service.py`
(core suite 335 passed/5 deselected, 309 pre-existing + 26 new); 15
new `tests_web/test_special_activity_repository.py` + 25 new
`tests_web/test_special_activity_api.py` (canonical single-process
`tests_web` 328 passed, 288 pre-existing + 40 new), zero
DB-reachability skips; existing Subject/Teacher/Class/Teaching
Assignment/Teacher Availability repository and API tests all
reconfirmed unregressed by the same full-suite runs; frontend 302
passed (fully unchanged -- zero frontend files touched), build clean;
Alembic unchanged at `cae76cba3c58`, single head, no drift.

A new local-only, unlocked review dataset,
`special-activity-review-school`/`ay-special-activity-review-2026`,
was created and is retained as durable local acceptance evidence,
live-validated against the real, running dev server's actual HTTP API.
**Seeded initial state** (a small deterministic problem built via the
existing TEST-ONLY `write_scheduling_problem`, not the full 40-period
pilot): one ORDINARY Subject `math`/"Mathematics", one CLUB Special
Activity `club_robotics`/"Robotics Club", one `ReservedBlock`
(`club_robotics_block`) referencing it, zero `Schedule`. During
acceptance: `GET` showed CLUB only; a same-name `ORDINARY` Subject
("Mathematics") did not conflict with an identically-named new CLUB
Special Activity, which was created and retained; a second new CLUB
Special Activity, "Debate Club", was created and retained; an exact
CLUB duplicate ("Robotics Club") was rejected 409; renaming the
referenced Special Activity ("Robotics Club" -> "STEM Lab") succeeded,
and `GET .../config` immediately reflected both the renamed `Activity`
and the synchronized `ReservedBlock.name`; deleting the
still-referenced Special Activity was rejected 409
`SPECIAL_ACTIVITY_IN_USE`; one further, separate temporary Special
Activity was created then successfully deleted, leaving no residue.
**Retained final state** (read-only re-inspected at closure,
unmutated since): four `Activity` rows -- `math`/"Mathematics"
(ORDINARY, unchanged), `club_robotics`/"STEM Lab" (CLUB, renamed),
`activity_f463e125da19438f9efaa2f00603d2c0`/"Debate Club" (CLUB, new),
`activity_167175007916472b843120305ef56183`/"Mathematics" (CLUB,
new); one `ReservedBlock`, `club_robotics_block`/"STEM Lab" (name
synchronized to its renamed Activity); **zero `Schedule`** throughout.

The nine pre-existing datasets -- the seven earlier canonical/review
datasets (`synthetic-school`, `synthetic-review-school`,
`teacher-crud-review-school`, `class-crud-review-school`,
`subject-crud-review-school`, `real-school-browser-smoke-school`,
`teacher-availability-review-school`) plus the two Availability C
acceptance datasets
(`teacher-availability-browser-choice-school`,
`teacher-availability-browser-required-school`) -- were snapshotted
before and after this entire run (now including `ReservedBlock` row
counts) and confirmed unchanged -- the same recorded snapshot/count
fields held identical for all nine; this was never claimed nor
performed as a byte-for-byte or row-by-row comparison, and the new
`special-activity-review-school` dataset is deliberately excluded from
that equality claim since it was intentionally created/mutated for
this acceptance.

Explicitly NOT part of this slice: `ReservedBlock` CRUD, the Reserved
Activities page, the School Setup Special Activities frontend tab, any
frontend change of any kind, Resource support, `ParticipantGroup`
support, any of the five preflight/write-validation corrections
recon identified for `ReservedBlock` itself (activity-kind validation,
class-slot collision, teacher-slot collision, Teacher-`UNAVAILABLE`
rejection, instructional-only slot rule) -- all of those belong to
Reserved A2, not implemented here. **Owner Decision #39 was NOT
created** -- the derived-name synchronization invariant is a
consistency consequence of the already-corrected product contract, not
a new product-semantics fork. Reserved A1 is **not** marked closed;
Reserved A2, the frontend (Reserved B), and browser/solver/timetable
acceptance (Reserved C) remain entirely unimplemented.

**Reserved Activities -- Reserved A1 (Special Activity catalog
backend): IMPLEMENTED, REVIEWED, real HTTP API review PASSED,
COMMITTED, MERGED to `main`, CLOSED.** Implementation commit
`0b64e115791e4da2bbc243d6d5ec848f28c9d064` ("feat: add special
activity backend"), fast-forward merged to `main` from
`feature/special-activity-backend` (now deleted). This entry is the
authoritative current-state record; the preceding historical entry
above captures the pending-review snapshot that led here and is
superseded by it.

Contract, unchanged from review and reconfirmed at closure: Special
Activity = `Activity(kind=CLUB)`, never a new domain entity, zero
schema/migration change. `name` is the sole mutable field; `kind`
always server-set to `CLUB`; duplicate rule exact/case-sensitive/
trimmed scoped to CLUB only (identical ORDINARY name never conflicts,
proven both ways); natural ID `activity_<uuid4().hex>`; ordinal is the
single `AcademicYear`-wide `Activity` sequence shared across both
kinds; delete blocked only by a genuine `ReservedBlock` reference
(`SPECIAL_ACTIVITY_IN_USE`); an `ORDINARY` target behaves as 404
`"Special activity not found"` on every route, with the repository
independently re-verifying `kind` regardless of the `validate`
callback. Mandatory derived-name invariant: renaming a Special
Activity atomically synchronizes every referencing `ReservedBlock.name`
in the same transaction (proven for single/multiple referencing
blocks, an untouched unrelated block, and zero partial synchronization
on a failed rename) -- `/config`'s own contract is unchanged and simply
reflects the synchronized values. Configuration lock and
generation-race protection (both orderings) both reuse existing,
unchanged shared infrastructure.

**Review dataset -- final retained state, re-confirmed read-only at
closure without further mutation:**
`special-activity-review-school`/`ay-special-activity-review-2026` was
seeded with one `ORDINARY` Subject ("Mathematics"), one `CLUB` Special
Activity ("Robotics Club"), one referencing `ReservedBlock`, and zero
`Schedule`. Its retained final state is four `Activity` rows --
`math`/"Mathematics" (`ORDINARY`, unchanged), `club_robotics`/"STEM
Lab" (`CLUB`, renamed from "Robotics Club" during review), `activity_
f463e125da19438f9efaa2f00603d2c0`/"Debate Club" (`CLUB`, created and
retained), `activity_167175007916472b843120305ef56183`/"Mathematics"
(`CLUB`, created and retained, proving same-name-as-ORDINARY
coexistence) -- one `ReservedBlock` (`club_robotics_block`) with its
`name` synchronized to "STEM Lab", and **zero `Schedule`**. A separate
temporary Special Activity created during review was deleted and
leaves no residue. Retained, not cleaned up, as durable local
acceptance evidence.

Final regression, reconfirmed identical to the pre-closure baseline:
pure `tests/test_special_activity_service.py` 26 passed; focused
`tests_web/test_special_activity_repository.py` +
`test_special_activity_api.py` 40 passed; core `tests -m "not slow"`
**335 passed/5 deselected**; canonical single-process `tests_web`
**328 passed, zero skips**; frontend **302 passed/18 files/zero
skips**, fully unchanged (zero frontend files touched), build clean;
Alembic unchanged at `cae76cba3c58`, single head, no drift. The nine
pre-existing datasets -- the seven earlier canonical/review datasets
plus the two Availability C acceptance datasets -- were re-snapshotted
at closure across the same recorded fields and remain identical to
both the pre- and post-review snapshots; the new review dataset
remains excluded from that equality claim. **Zero production source
changes beyond the reviewed scope**, **zero migration**, **zero
frontend change**, **zero domain/ORM change**, **zero solver
production change**.

**Owner Decision #38 remains authoritative and unchanged. Owner
Decision #39 was not created** -- the derived-name synchronization
invariant is a cross-feature consistency rule, not a new
product-semantics fork. **Reserved A2 (`ReservedBlock` backend CRUD +
validation/preflight) is NOT implemented. Reserved B (frontend) is NOT
implemented. Reserved C (browser/solver/timetable acceptance) is NOT
executed.** Nothing was pushed to any remote. **Next planned slice:
Reserved A2 -- `ReservedBlock` backend CRUD + validation/preflight.**

**Reserved Activities -- Reserved A2 (Reserved Activity /
`ReservedBlock` backend): CLOSED ON MAIN.** Implementation commit
`ab15e6a` (`ab15e6a0ea645b6160d62941eb2609b87b09a96f`, "feat: add
reserved activity backend"), fast-forwarded onto `main` directly after
`8bfd9e8` (no merge commit). "Reserved Activity" is not a new
domain entity -- it is the user-facing name for `ReservedBlock` (see
`domain/blocks.py`), exactly mirroring "Special Activity"'s own
relationship to `Activity(kind=CLUB)`. Reuses the existing `ReservedBlock`
domain object and `reserved_block`/`reserved_block_class_section`/
`reserved_block_slot` ORM tables verbatim -- **zero schema change, zero
migration, no new domain entity, no new enum.**

CRUD contract: POST/PUT accept the complete mutable aggregate
(`special_activity_id`, `class_section_ids`, `teacher_id`, `slots`) as
one whole-aggregate replacement -- never a partial patch, and
`teacher_id` is required-but-nullable so POST/PUT share one identical
request shape. `ReservedBlock.name` is always server-derived from the
referenced Special Activity's current `name`, never accepted as
input. Natural ID: server-generated `reserved_block_<uuid4().hex>`
(named after the persisted entity, mirroring `activity_`/`teacher_`/
`class_` convention, not the presentation label). Ordinal: `ReservedBlock`'s
own `AcademicYear`-scoped sequence (never shared with any other
table). Canonical child ordering, proven load-bearing for Owner
Decision #36: `class_section_ids` sorted by the referenced
`ClassSection`'s own authoritative persisted ordinal; `slots` sorted
by `Day.index` then `Period.index` -- always recomputed at write
time regardless of request order, so two semantically-identical
writes persist and reload as byte-identical `SchedulingProblem.
reserved_blocks` tuples (frozen-dataclass equality is positional).
Update replaces `ReservedBlockClassSection`/`ReservedBlockSlot`
children wholesale (delete-and-reinsert in canonical order, one
transaction) -- there is no independent child identity to diff
against.

Five semantic invariants, each enforced at both application write-time
and independent preflight defense-in-depth, closing the asymmetric
validation gap the recon phase identified: (1) `special_activity_id`
must resolve to `Activity(kind=CLUB)` -- preflight
`RESERVED_BLOCK_NON_CLUB_ACTIVITY` (mirrors
`NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY`'s own established
naming convention exactly, verified against it directly rather than
assumed), application-facing `NonSpecialActivityTargetError` ->
422 `NON_SPECIAL_ACTIVITY_TARGET`, deliberately never leaking raw
`ActivityKind`/`CLUB`/`ORDINARY` vocabulary in its own response and
never reusing Teaching Assignments' unrelated, unaltered
`NON_ORDINARY_ACTIVITY_TARGET` contract; (2) every slot must be
instructional -- `RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT`; (3) a
teacher-attached block may never use a slot where that Teacher is
`UNAVAILABLE` -- `RESERVED_BLOCK_TEACHER_UNAVAILABLE` (`PREFER_NOT`
and `AVAILABLE`/absent are both allowed, with zero solver penalty --
`ReservedBlock` remains fixed occupancy with zero CP-SAT variables,
confirmed unchanged); (4) two different `ReservedBlock`s may never
claim the same `(ClassSection, Day, Period)` --
`RESERVED_BLOCK_CLASS_SLOT_COLLISION`; (5) two different
teacher-attached `ReservedBlock`s may never claim the same `(Teacher,
Day, Period)` -- `RESERVED_BLOCK_TEACHER_SLOT_COLLISION`. Plus two
same-block structural defense-in-depth diagnostics mirroring
`DUPLICATE_TEACHER_AVAILABILITY_CELL`'s own precedent exactly:
`DUPLICATE_RESERVED_BLOCK_CLASS_SECTION`/`DUPLICATE_RESERVED_BLOCK_SLOT`.
All five semantic checks are implemented ONCE, in `validation/
preflight.py`, and reused by the application layer via a candidate-
diff pattern (`reserved_activity_rules.py` constructs an in-memory
candidate `ReservedBlock`, replaces it into a copy of the freshly-
loaded `SchedulingProblem`, and treats any newly-appearing preflight
code as blocking) -- mirroring `teaching_assignment_rules.
new_validation_errors`'s own established architecture exactly, so the
write-time check and the independent verifier can never silently
diverge. The collision scan is a direct, deterministic traversal
(`ReservedBlock` problem order, then authoritative `ClassSection`
order / `Day.index`-`Period.index` order within each block) --
deliberately never `ProblemIndex.reserved_class_slots`/
`reserved_teacher_slots`, which are plain last-writer-wins solver
lookups, never a validator; each collision emits exactly one
directional diagnostic (later block -> earlier first-owner block),
never a mirrored pair, and a block can never collide with its own
prior self because the write-time candidate always *replaces* the
target block rather than duplicating it.

Discovered, unrelated regression during preflight work, fixed:
`tests/test_reoptimize.py::test_new_reserved_block_conflicting_with_
reference_is_repaired` constructed its own hand-built `Activity(
"club_chess", "Chess")` without `kind=ActivityKind.CLUB` (a
pre-existing Phase-2C test fixture bug, invisible before this slice
since nothing validated `ReservedBlock.activity_id`'s kind) -- the new
`RESERVED_BLOCK_NON_CLUB_ACTIVITY` preflight check correctly caught
it; the test's own fixture was corrected (`kind=ActivityKind.CLUB`
added), not the new check weakened.

Repository/service naming: a new, separate `ReservedActivityRepository`
port and `SqlAlchemyReservedActivityRepository` adapter -- application
naming uses the user-facing "Reserved Activity" term throughout;
domain/ORM naming (`ReservedBlock`, `reserved_block`) is unchanged.
Unlike every prior write port, `create`/`update` return the written
aggregate's own canonically-ordered fields directly (never `None`) --
the persisted child order depends on canonicalization only the
repository performs, so returning it avoids duplicating that ordering
logic in the caller. ORM defense-in-depth mirrors
`SqlAlchemySpecialActivityRepository`'s own pattern: every reference
(Special Activity, Teacher, ClassSection, Day, Period) is independently
re-resolved scoped to the *same* `academic_year_id`, and the Special
Activity's `kind` is independently re-verified regardless of what
`validate` already confirmed -- proven by dedicated repository tests
using a deliberately permissive fake `validate` callback. Delete is a
leaf-aggregate operation: `ReservedBlockClassSection`/
`ReservedBlockSlot` children cascade at the DB level; the referenced
Special Activity/Teacher/ClassSection/Day/Period rows, and
`schedule_entry`'s own `RESTRICT` FK to `reserved_block.id`, are never
reached (the latter is structurally unreachable here since the
configuration lock already forbids this delete outright once any
`Schedule` exists) -- proven directly from schema.

New routes: `GET/POST /schools/{school_id}/years/{year_id}/
reserved-activities`, `PUT/DELETE .../reserved-activities/
{reserved_activity_id}` -- deliberately never `/reserved-blocks` in
the dedicated API (raw `/config` keeps its own unrenamed
`reserved_blocks` field, completely unchanged and unretrofitted). GET
projection (`ReservedActivityProjectionService`) loads exactly ONE
`SchedulingProblem` snapshot and derives every list from that same
object -- deliberately never composes `SpecialActivityProjectionService`
or any other nested projection service, avoiding an internally
incoherent page. Every `reserved_activities` item is normalized (bare
`special_activity_id`/`class_section_ids`/`teacher_id` references
only, resolved by the consumer against the same response's own
top-level `special_activities`/`teachers`/`class_sections` catalogs) --
never denormalized display names, `ReservedBlock.name`, `kind`, or
`ordinal`. `periods` returns every period with `is_instructional`
(matching Teacher Availability's own precedent); writes accept
instructional periods only, server-authoritative regardless of what a
future frontend offers.

Test gate: 46 new pure `tests/test_reserved_activity_service.py`
(covering both `ReservedActivityService` and
`ReservedActivityProjectionService`) + 17 new preflight tests in
`tests/test_preflight.py` (core suite 398 passed/5 deselected, 335
pre-existing + 63 new); 30 new
`tests_web/test_reserved_activity_repository.py` (including 5 dedicated
cross-AcademicYear defense-in-depth tests added during the pre-closure
audit) + 38 new `tests_web/test_reserved_activity_api.py` (canonical
single-process `tests_web` 396 passed, 328 pre-existing + 68 new), zero
DB-reachability skips; existing Special Activity/Subject/Teacher/
Class/Teaching Assignment/Teacher Availability repository and API
tests all reconfirmed unregressed by the same full-suite runs;
frontend 302 passed/18 files, zero skips (fully unchanged -- zero
frontend files touched), build clean; Alembic unchanged at
`cae76cba3c58`, single head, no drift.

**Pre-closure audit result: A. RESERVED A2 PRE-CLOSURE AUDIT PASSED**
(run twice, verbatim-identical results both times, zero drift). Cross-
AcademicYear repository defense-in-depth was explicitly proven, not
merely asserted, via dedicated tests using a deliberately permissive
fake `validate` callback so the proof isolates the repository's own
independent re-scoping: cross-AY UPDATE target rejected, cross-AY
DELETE target rejected, UPDATE onto an `ORDINARY` activity rejected
even with a permissive validator, UPDATE onto a Special Activity from
another `AcademicYear` rejected, and a representative other-AY
`ClassSection` reference rejected on CREATE -- all five confirmed as
zero-partial-mutation (no row from either `AcademicYear` was altered
by a rejected write). Owner Decision #36's generation-vs-write race
protection was reconfirmed unchanged: `SchedulingProblem.
reserved_blocks` was already part of the frozen-dataclass equality
`persist_initial_version` uses for stale-problem detection, requiring
zero changes to that mechanism.

**Final verification baseline (post-merge, on `main`):** core `pytest
-m "not slow"` 398 passed/5 deselected; `tests_web` 396 passed, zero
skips; frontend `vitest` 302 passed/18 files, zero skips; frontend
build clean; Alembic `cae76cba3c58`, one head, no drift.

A new local-only, unlocked review dataset,
`reserved-activity-review-school`/`ay-reserved-activity-review-2026`
(3 days, 4 instructional periods + 1 non-instructional period, 3
Teachers, 3 ClassSections, 2 CLUB Special Activities + 1 ORDINARY
Subject, Teacher A carrying one `UNAVAILABLE` and one `PREFER_NOT`
row, one pre-existing non-conflicting `ReservedBlock`, zero
`Schedule`, deliberately zero `TeachingRequirement`s since Reserved
Activity acceptance needs no full class occupancy and this write
surface's own validation already treats `CLASS_OCCUPANCY_MISMATCH` as
non-blocking, mirroring Teaching Assignments' identical save-time
validation boundary), was created and is retained as durable local
acceptance evidence: live-validated against the real, running dev
server's actual HTTP API across all 19 required proof points -- exact
GET projection shape; a valid teacherless and a valid teacher-attached
create; class-request-order and slot-request-order both reversed on
input, both persisted/returned in canonical order; in-request
duplicate class and duplicate slot both rejected; an ORDINARY
`special_activity_id` target rejected 422 `NON_SPECIAL_ACTIVITY_TARGET`
with zero raw `CLUB`/`ORDINARY`/`actual_kind` leak; a non-instructional
slot rejected; a Teacher-`UNAVAILABLE` slot rejected (bundled
correctly alongside a simultaneously-true class-slot collision in the
same response, proving multi-diagnostic bundling); a Teacher-
`PREFER_NOT` slot accepted; clean class-slot and teacher-slot
cross-block collisions each rejected with the exact expected
`conflicting_reserved_block_id`; a `PUT` whole-aggregate replacement
that also changes the referenced Special Activity, immediately
reflected in `/config` with the recomputed `ReservedBlock.name`; a
`DELETE` immediately reflected as absent in `/config`; zero `Schedule`
throughout.

**Seeded initial state** (as built by the TEST-ONLY writer, before any
HTTP review activity): exactly one `ReservedBlock`,
`club_debate_block` (Debate Club, class `c1`, no teacher, slot
`(tue, p1)`). **Final retained state** (re-confirmed read-only,
directly from PostgreSQL, at pre-closure audit -- never inferred):
`club_debate_block` was renamed via the PUT-whole-aggregate-
replacement proof step (its Special Activity changed to `club_art`,
recomputing its name to "Art Club") and was then removed by the
`DELETE` proof step -- zero rows with that original natural ID remain.
Five other blocks were created and retained across the review
sequence (the teacherless create, the teacher-attached create, the
reversed-class-order create, the reversed-slot-order create, and the
Teacher-`PREFER_NOT`-allowed create): `reserved_block_e0556f53...`
(Art Club, class `c2`, no teacher, `(mon, p3)`),
`reserved_block_1de0ed17...` (Art Club, class `c3`, teacher `t_c`,
`(wed, p4)`), `reserved_block_74aedba8...` (Debate Club, classes
`c1`/`c2`/`c3`, no teacher, `(wed, p1)`), `reserved_block_60a0304d...`
(Debate Club, class `c1`, no teacher, `(mon, p1)` and `(wed, p4)`),
and `reserved_block_52cab4de...` (Art Club, class `c1`, teacher
`t_a`, `(mon, p2)`). **Final `ReservedBlock` count: exactly 5.
Final `Schedule` count: 0.** (A prior informal summary of this
session miscounted the retained total as four newly-created blocks;
the actual, PostgreSQL-confirmed total is five -- this entry is the
authoritative correction.)

All ten pre-existing datasets -- the seven earlier
canonical/review datasets, the two Availability C acceptance datasets,
and Reserved A1's own `special-activity-review-school` (itself now a
pre-existing dataset relative to this slice) -- were snapshotted
before and after this entire run (including `ReservedBlock` counts)
and confirmed unchanged -- the same recorded snapshot/count fields
held identical for all ten; this was never claimed nor performed as a
byte-for-byte or row-by-row comparison, and the new
`reserved-activity-review-school` dataset is deliberately excluded
from that equality claim since it was intentionally created for this
acceptance.

Explicitly NOT part of this slice: the Reserved Activities frontend
page, any frontend change of any kind, Resource support,
`ParticipantGroup` support, multiple Teachers per block, any
recurrence/flexible-placement capability, any timetable-projection
production change (Class/Teacher Timetable already displayed
`ReservedBlock` entries correctly before this slice and remain
untouched), any solver production change (`ProblemIndex`/
`model_builder`/`result_builder`/`verifier` are all byte-for-byte
unchanged -- the five new invariants are pure preflight/application
validation that prevents an invalid `ReservedBlock` from ever reaching
the solver, never a change to how the solver itself treats one).
**Owner Decision #39 was NOT created** -- every open question this
slice resolved (naming, ordering, response shape, error-type
granularity, repository boundary) was settled by direct, load-bearing
precedent already present in the codebase, never a genuine
code-unresolvable product-semantics fork.

**Reserved A2 is CLOSED ON MAIN** (implementation commit `ab15e6a`).

**Reserved Activities -- Reserved B (Special Activities + Reserved
Activities frontend): CLOSED ON MAIN.** Implementation commit
`81aef7f` (`81aef7f73460e5ae5828a43dcdd6f1c3942bcdd2`, "feat: add
reserved activities frontend"), fast-forwarded onto `main` directly
after `dc22655` (no merge commit). Frontend-only -- zero backend
production change, zero backend test change, zero migration, zero
domain change, zero solver change, zero `api/types.ts` change (the one
genuinely shared type this feature needs, `ValidationDiagnostic`, is
imported, never redefined or modified there).

**B1 -- School Setup -- Special Activities tab.** A fourth local,
non-routed tab (`Teachers, Classes, Subjects, Special Activities`),
`SpecialActivitiesPanel.tsx`, built as a structural copy of
`SubjectsPanel.tsx`: independent GET on mount, create/rename/delete via
inline contained forms (no modal, no `window.confirm`), authoritative
GET after every write, lock/lock-race/stale-authority states, and
`SPECIAL_ACTIVITY_IN_USE` mapped to "This Special Activity is used by a
Reserved Activity and cannot be deleted." -- never leaking
`RESERVED_BLOCK`. A new, narrow, typed, one-shot School Setup
tab-target mechanism (`SchoolSetupPage.tsx`'s exported `TabKey`/
`SchoolSetupNavigationState`/`isSchoolSetupTabKey`) lets another page
land on this tab pre-selected via `navigate(..., {state:
{requestedTab: "..."}})`, consumed once via `navigate(path, {replace:
true, state: null})` so it never replays on Back/reload; a direct
visit or reload always defaults to Teachers, and normal in-page tab
clicks never touch `location.state`.

**B2 -- Reserved Activities page** (`/configuration/reserved-activities`,
fifth and last flat nav link, after Teaching Assignments --
`ReservedActivitiesPage.tsx`). Saved records render as stacked summary
cards (`ReservedActivityCard.tsx`), never table rows, so multiple
Classes/slots stay readable. Exactly ONE full-width contained Add/Edit
editor panel (`ReservedActivityEditor.tsx`, never a modal/drawer --
`AssignmentDrawer`'s narrow fixed-width side panel cannot hold the slot
matrix without horizontal scroll): Special Activity single-select (no
first-item default), Classes as a `fieldset`/checkbox list, Teacher
single-select defaulting to "No teacher", and
`ReservedActivitySlotGrid.tsx` -- a desktop Period-rows x Day-columns
checkbox matrix plus a per-Day mobile stacked layout, both always in
the DOM with one `@media (max-width:640px)` CSS toggle (reusing
`AvailabilityGrid`'s proven layout/responsive *pattern* only, never the
component itself -- a Reserved Activity slot is set membership, so
every cell is a real `<input type="checkbox">`, never a 3-state status
button/`aria-pressed`). Desktop/mobile checkboxes for the same slot are
two distinct DOM nodes with unique `reserved-slot-desktop-{day}-{period}`/
`reserved-slot-mobile-{day}-{period}` ids, each with its own
`<label htmlFor>` (visually hidden via the existing `.sr-only` on
desktop, visible on mobile); non-instructional periods are never
rendered. The submitted `class_section_ids`/`slots` are always
canonically ordered (catalog order; Day-index-then-Period-index) from
internal `Set`-based selection state, regardless of click order --
proven by a dedicated test that toggling a checkbox off and back on
never spuriously marks an edit-mode draft dirty.

A page-level prerequisite surface (`computeMissingPrerequisites`)
replaces the Add toolbar -- never a broken/disabled editor -- whenever
`special_activities`, `class_sections`, or (`days`/instructional
`periods`) is empty; multiple missing items render as one combined
surface, each naming its own missing requirement, with a School Setup
tab-target link for the first two and no fabricated destination for
the calendar case (no Calendar editor exists in this phase). Locked/
stale states take precedence over the prerequisite surface (computed
only once fresh and unlocked) and reuse the existing `.lock-banner`/
`.stale-banner` exactly.

Write-outcome classification extends the established
`SubjectsPanel`/`TeachingAssignmentsPage` pattern
(`ok`/`lockRace`/`notFound`/inline-error) with one new `referenceStale`
outcome for `UNKNOWN_REFERENCE`/`NON_SPECIAL_ACTIVITY_TARGET`: the
write did not commit, the editor closes and its draft is discarded
immediately (never preserved, unlike an ordinary
`INVALID_RESERVED_ACTIVITY` failure) -- a deliberate correction to the
literal single-reference precedent, since a Reserved Activity draft
carries five independently-stale-able references. A successful
refetch shows a transient, dismissible "The configuration changed.
Review the latest data and try again."; a failed one enters the same
stale-authority state as every other mutation path. `SCHEDULING_
CONFIGURATION_LOCKED` and a successful-write-then-failed-refresh both
reuse the exact existing lock-race/stale sequencing verbatim -- the
editor already closes immediately on a confirmed successful mutation,
before the authoritative refetch is even attempted, so a failed
refetch can never leave a re-submittable stale editor on screen.

`INVALID_RESERVED_ACTIVITY` diagnostics (`RESERVED_BLOCK_REQUIRES_
CLASS_SECTION`/`_REQUIRES_SLOT`, both `DUPLICATE_RESERVED_BLOCK_*`,
`RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT`, `_TEACHER_UNAVAILABLE`, both
`_COLLISION` codes) are mapped to human text by resolving every
natural ID in each diagnostic's `context` against the current
projection's own catalogs -- a collision additionally resolves
`conflicting_reserved_block_id` to that other reservation's own
Special Activity name (e.g. "Conflicts with the existing Debate Club
reservation: 8A, Monday P1"), falling back to "Conflicts with another
existing Reserved Activity" if that lookup fails; no raw ID is ever
shown. A saved record with an unresolvable reference (legacy/corrupt
data, not expected from the real single-snapshot projection) falls
back to "Unknown Special Activity"/"Unknown Class"/"Unknown Teacher"/
"Unknown time slot", stays read-only-renderable and Delete-able, and
refuses to open its editor (a small inline notice instead).

Test gate (frontend-only) -- reported here as **affected-file focused
execution totals**, not "new tests" (`SchoolSetupPage.test.tsx`/
`App.test.tsx` both already carried pre-existing tests before this
slice): **B1 focused execution: 42 passed across 3 files**
(`specialActivities.test.ts`, `SpecialActivitiesPanel.test.tsx`,
`SchoolSetupPage.test.tsx`). **B2 focused execution: 77 passed across
6 files** (`reservedActivities.test.ts`,
`ReservedActivitySlotGrid.test.tsx`, `ReservedActivityCard.test.tsx`,
`ReservedActivityEditor.test.tsx`, `ReservedActivitiesPage.test.tsx`,
`App.test.tsx`). **Combined focused execution (the union of both
groups' affected files, `SchoolSetupPage.test.tsx` and
`ReservedActivitiesPage.test.tsx` run together): 119 passed across 9
distinct files.** (An earlier draft of this entry mischaracterized this
as a single "focused B1+B2 gate 76 passed across 6 files" -- that
conflated the B2-only group with the combined B1+B2 total and used a
pre-audit count; this entry is the corrected, current figure, which
also reflects two pre-closure-audit additions: an executable proof
that the School Setup tab-target `location.state` is actually cleared
to `null`, and a two-record proof that opening one Reserved Activity's
editor disables Add, the *other* record's Edit, and *both* records'
Delete.) The authoritative **full suite total is 402 passed, 25
files, zero skips** (400 pre-audit + the same 2 audit-added tests
above -- no other test was added, removed, or changed); build clean.
No backend tracked files changed in Reserved B; backend regression
baselines remained 398 passed/5 deselected and `tests_web` 396
passed/zero skips. Alembic remained `cae76cba3c58` with one head and
no drift. A lightweight manual visual pass against the real
running dev server (`synthetic-school`/`ay-2026`, already locked, with
two pre-existing Reserved Blocks) confirmed live end-to-end rendering
of both the Special Activities tab and the Reserved Activities card
list, correct nav/tab order, and correct locked-state disabling --
this was not Reserved C acceptance, mutated no data, and created no
Schedule.

**Owner Decision #39 was NOT created** -- every choice here (card vs.
table, checkbox matrix vs. token list, contained panel vs. drawer,
`location.state` vs. query/hash) was resolved by direct usability
reasoning and existing precedent during the closed frontend contract
gates, never a genuine product-semantics fork.

**Reserved B is CLOSED ON MAIN** (implementation commit `81aef7f`).

**Reserved Activities -- Reserved C (real-browser / persistence /
solver / timetable / lock acceptance): PASSED.** Acceptance ran across
two sessions against one retained, local-only dataset
(`reserved-activity-c-acceptance-school`/
`ay-reserved-activity-c-acceptance-2026`, created via the existing
TEST-ONLY `tests_web/support/problem_writer.py` writer -- 2 ClassSections,
3 Teachers, 2 ORDINARY Activities, 2 Special Activities, 2 instructional
Days x 3 instructional Periods): the first session completed every CRUD/
collision/validation proof and was then explicitly **BLOCKED** solely by
a browser-viewport-tooling gap (`resize_window` proved a no-op in that
session's environment -- verified via `window.innerWidth` staying fixed
regardless of the requested size, including a large-size control). That
block is preserved here as real acceptance history, not erased: nothing
was faked or skipped in its place. This session resumed from the
retained pre-generation checkpoint (2 ReservedBlocks, 0 Schedules) and
resolved the gap.

**Deliberate 3-teacher construction (a considered deviation from a
2-teacher recommendation):** Teacher A is reserved with 8B (Debate Club,
Monday/Period 1, PREFER_NOT) and also carries 8A's ordinary Math load --
but Teacher A is additionally UNAVAILABLE at Monday/Period 2 (to prove
hard rejection), which makes it mathematically impossible for Teacher A
alone to cover all 5 of 8A's non-reserved slots (only 4 remain
available). A third Teacher (Teacher C) was added solely to cover that
one resulting 8A gap (1 Science period at Monday/Period 2) -- verified
feasible by a direct `run_preflight`/`solve()` dry-run against the
exact intended final `ReservedBlock` state before any browser step:
**preflight errors = 0, solver status = OPTIMAL, total_soft_penalty =
0**, with the solver's own placement matching the intended shape
exactly (zero slack anywhere else, so the dry-run result was fully
deterministic).

**Viewport gap resolution:** a separate, genuinely narrow-viewport real
Chrome instance was launched locally (`google-chrome --headless=new
--remote-debugging-port=... `, an already-installed system browser --
no project dependency added, no package.json/package-lock touched) and
driven directly over the Chrome DevTools Protocol (`Emulation.
setDeviceMetricsOverride`, using the system Python's already-installed
`websockets`/`requests` packages -- outside the project's own
dependency tree entirely) against the same live Vite dev server
rendering the real application. Confirmed live: `innerWidth: 375,
clientWidth: 360` -- a genuine, CDP-verified narrow viewport, not
inferred from a screenshot. At that width: the desktop Period x Day
matrix was hidden (`display:none`) and the mobile per-Day sections were
shown; the Special Activity select, Classes fieldset, Teacher select,
and Time-slots fieldset (with a correctly-labelled
`reserved-slot-mobile-mon-p1` checkbox, accessible name "Monday, Period
1") all rendered correctly within the editor; a scan restricted to the
editor's own DOM subtree found **zero elements overflowing the
viewport**. Save/Cancel remained fully on-screen and reachable. Desktop
width (1280px) was re-confirmed in the same session: matrix visible,
mobile hidden. A **93px page-level overflow was found** (`clientWidth
360` vs `scrollWidth 453`), traced exclusively to the shared `AppShell`
top nav bar (`.app-nav-link`), not to any Reserved Activities content
-- confirmed by reproducing the identical overflow on the pre-existing,
unrelated Teacher Availability page, proving it predated this feature.

**Correction (post-closure technical review):** the session that
produced this evidence classified that 93px overflow as
"pre-existing/out-of-scope" and closed Reserved C anyway. That
classification was **too permissive** -- Reserved C's own explicit
acceptance criterion required no material page-level horizontal
overflow at a genuine <=640px viewport, full stop, regardless of
whether the responsible markup happened to be Reserved-Activities-owned
or shared `AppShell` chrome the feature merely renders underneath. The
overflow was therefore a **real Reserved C acceptance blocker**, not an
acceptable pre-existing condition, and required a corrective fix before
Reserved C could be validly considered passed. No Reserved Activities
business logic was ever defective -- the defect was entirely in shared
`AppShell` responsive layout (`.app-nav` had no `flex-wrap`, so its
five flat links stayed on one un-wrapping row wider than a narrow
viewport). This was corrected in a dedicated follow-up commit,
`865138d` (`865138dabbb7f1bec395aa64d624c530ea115880`, "fix: wrap
mobile app navigation"): a single `@media (max-width:640px) { .app-nav
{ flex-wrap: wrap; gap: 0.6rem 1rem; } }` rule -- all five links remain
directly visible and in the same order, wrapping cleanly into multiple
rows at narrow widths with no hidden items, no hamburger/dropdown, and
no JS viewport handling; desktop layout, order, and appearance are
completely unaffected above 640px. `AppShell.tsx` markup was unchanged
-- purely a CSS fix. One new regression test was added
(`App.test.tsx`, confirming the nav's `app-nav` CSS hook and all five
links remain present) -- not a rewrite of the existing nav-order/
active-state tests, which already covered link count/order/active
state and needed no change.

**Re-run narrow acceptance (after the fix, real CDP-driven Chrome,
same mechanism as above), with zero tolerance for the previous
overflow:**
- Reserved Activities @375: `innerWidth 375 / clientWidth 360 /
  scrollWidth 360`.
- Teacher Availability @375: `innerWidth 375 / clientWidth 360 /
  scrollWidth 360`.
- School Setup @375: `innerWidth 375 / clientWidth 360 / scrollWidth
  360`.
- All three: `scrollWidth` exactly equals `clientWidth` -- **zero**
  page-level horizontal overflow, not merely reduced.
- All five flat-nav links present, correct order, on every page; the
  nav wraps cleanly into multiple rows with no clipping and no overlap
  with page content (nav bottom edge above the page content's own top
  edge on every page checked).
- Reserved Activities editor (opened against an unlocked, already-
  existing local dataset, `reserved-activity-review-school` --
  `reserved-activity-c-acceptance-school` was correctly left
  untouched/still locked): a full-document scan (not merely the
  editor's own subtree) found zero overflowing elements; mobile
  per-Day slot layout shown, desktop matrix hidden; Save/Cancel both
  reachable within the viewport; Cancel exercised without saving.
- Desktop @1280px reconfirmed in the same corrective session: nav
  stays in exactly one row, exact link order preserved, desktop matrix
  visible, mobile layout hidden -- zero desktop regression.

This is a genuine corrective re-run of the one failed acceptance gate,
not a restart of Reserved C: every other piece of Reserved C evidence
recorded above and below (CRUD/collision/validation proofs, solver
generation, independent verifier, timetable placements, exact-full
occupancy, `ScheduleEntry` persistence, UI/API lock, pre-existing
dataset safety) remains valid as originally recorded and was
deliberately not repeated.

**Live acceptance evidence (this session, real browser + real API +
direct PostgreSQL, `synthetic-school` and all other pre-existing
datasets confirmed untouched):**
- Class-collision rejection: "Conflicts with the existing Assembly
  reservation: 8A, Monday Period 1." (draft preserved).
- Teacher-UNAVAILABLE rejection: "Teacher A is unavailable on Monday
  Period 2." (draft preserved).
- PREFER_NOT acceptance: the Debate Club/8B/Teacher A/Monday-Period-1
  reservation **created successfully** despite PREFER_NOT.
- Full temporary Special-Activity/Reserved-Activity lifecycle (create,
  rename, in-use delete blocker, whole-aggregate edit across three
  dimensions, delete, then the now-unblocked Special Activity delete)
  all passed with human-language messages only, never leaking `CLUB`/
  `ORDINARY`/`ReservedBlock`/`RESERVED_BLOCK`.
- Persistence reconciliation: PostgreSQL's `reserved_block`/
  `reserved_block_class_section`/`reserved_block_slot` rows (canonical
  ordinal 0 each) and the raw `/config` endpoint's `reserved_blocks`
  both matched the browser-visible final state exactly (Assembly/8A/no
  teacher/Mon-P1; Debate Club/8B/Teacher A/Mon-P1) before generation.
- **Real-browser generation** (`Timetable` page, real "Generate
  schedule" button): succeeded. Persisted `schedule_version` row:
  **`solver_status = OPTIMAL`, `total_soft_penalty = 0`** -- confirming
  Teacher A's PREFER_NOT reservation carries zero solver penalty, exactly
  as designed (`ReservedBlock` remains fixed occupancy with zero CP-SAT
  decision variables).
- **Independent verifier**: `generate_schedule_service.py`'s own code
  makes persistence structurally impossible unless
  `verification.verifier.verify(...)` returns `passed=True` first (a
  hard gate before any write, confirmed by direct code inspection) --
  the persisted `Schedule`/`schedule_version` row's mere existence is
  therefore conclusive verifier-pass evidence, not merely "CP-SAT said
  OPTIMAL."
- Class timetables (real browser): 8A Monday/Period 1 = "Assembly
  RESERVED" exactly, remaining 5 slots = 4x Mathematics/Teacher A + 1x
  Science/Teacher C. 8B Monday/Period 1 = "Debate Club RESERVED" /
  Teacher A exactly, remaining 5 slots = 5x Science/Teacher B. Neither
  reservation was moved by the solver.
- Teacher A's timetable (real browser): Monday/Period 1 = "Debate Club
  RESERVED" only (no simultaneous ordinary 8A lesson); Monday/Period 2 =
  empty (the UNAVAILABLE slot, correctly never assigned); "Assembly"
  (teacherless) never appears anywhere in Teacher A's timetable.
- Exact-full occupancy, confirmed via direct PostgreSQL aggregation
  over `schedule_entry` joined through both `teaching_requirement`/
  `participant_group_class_section` and `reserved_block_class_section`:
  **exactly 6 occupied cells for `class_8a` and exactly 6 for
  `class_8b`** -- zero empty cells, zero double occupancy.
- `ScheduleEntry` persistence: **12 rows total** -- 10 `source=REQUIREMENT`
  (the ordinary lessons) + 2 `source=RESERVED_BLOCK` (one per fixed
  Reserved Activity, each carrying its own `reserved_block_id` and the
  exact Monday/Period-1 day/period).
- Post-generation UI lock: both Reserved Activities and Special
  Activities surfaces showed the identical banner text "Scheduling
  configuration is locked because a schedule already exists.", with
  every record readable and every Add/Edit/Delete/Rename control
  disabled.
- Direct API lock: `POST`/`PUT`/`DELETE .../reserved-activities` and
  `POST .../special-activities` each returned exactly `409
  {"code":"SCHEDULING_CONFIGURATION_LOCKED", ...}`; `GET` remained `200`
  with `configuration_locked: true`; PostgreSQL counts (`ReservedBlock`
  2, `Activity` 4, `Schedule` 1, `ScheduleEntry` 12) were identical
  before and after every rejected write -- zero partial mutation.

**Final retained C dataset state** (kept, not cleaned up, as durable
acceptance evidence): Teachers 3, ClassSections 2, ORDINARY Activities
2, Special Activities 2, TeachingRequirements 3, TeacherAvailability
rows 2, ReservedBlocks 2, Schedules 1, ScheduleEntries 12.

**Pre-existing dataset safety:** every dataset that existed before
Reserved C (`synthetic-school`, `synthetic-review-school`,
`teacher-crud-review-school`, `class-crud-review-school`,
`subject-crud-review-school`, `real-school-browser-smoke-school`,
`teacher-availability-review-school`,
`teacher-availability-browser-choice-school`,
`teacher-availability-browser-required-school`,
`special-activity-review-school`, `reserved-activity-review-school`)
was snapshotted across the same recorded practical count fields
(Teacher/Class/ORDINARY/CLUB/TeachingRequirement/TeacherAvailability/
ReservedBlock/Schedule) -- the same recorded snapshot/count fields held
identical for all eleven, including `reserved-activity-review-school`'s
own `ReservedBlock` count of exactly 5, matching its own already-closed
A2 acceptance record precisely. No write request was ever directed at
any of these datasets during Reserved B or C. This was never claimed
nor performed as a byte-for-byte or row-by-row comparison.

Full regression reconfirmed after the corrective mobile-nav fix (current
baseline, superseding the pre-fix numbers this session originally
observed): core 398 passed/5 deselected, `tests_web` 396 passed/zero
skips, **frontend 403 passed/25 files/zero skips** (402 + the one new
`App.test.tsx` nav-CSS-hook regression test added by the fix commit --
not 403 "new tests"), build clean, Alembic `cae76cba3c58`/one head/no
drift. Zero production/test/frontend file changed by the acceptance
*steps themselves* (browser interaction, generation, lock checks,
snapshots -- the only local change those steps made was
`frontend/.env.local`'s gitignored `VITE_SCHOOL_ID`/
`VITE_ACADEMIC_YEAR_ID`, restored to `synthetic-school`/`ay-2026`
after each use); the two-file mobile-nav fix itself is tracked
separately as its own commit, `865138d`, exactly as recorded above --
it is the one and only production change this closure required.

**Owner Decision #39 remains NOT created** -- the 3-teacher dataset
construction was a test-fixture engineering decision to keep the
acceptance dataset genuinely solver-feasible, never a product-semantics
question.

**Closure history, corrected forward, not erased:** commit `072aea7`
("docs: close reserved activities phase") recorded the *first* Reserved
Activities phase closure. A subsequent technical review found that
closure's own narrow-acceptance evidence had applied Reserved C's
"no material page-level horizontal overflow" criterion too
permissively -- the 93px `AppShell` nav overflow documented above was
waved through as "pre-existing/out-of-scope" rather than treated as
the acceptance blocker it actually was under that criterion. The
mobile-nav fix commit `865138d` closed that one remaining defect; no
other part of Reserved C's original evidence (CRUD/collision/
validation proofs, solver generation, independent verifier, timetable
placements, exact-full occupancy, persistence, UI/API lock,
pre-existing dataset safety) was ever invalid, and none of it was
repeated -- this was a targeted correction of one acceptance gap, not
a restart of Reserved C. With that gap corrected and re-verified at a
genuine, CDP-confirmed <=640px viewport, **Reserved C's acceptance
criteria are now fully satisfied, and the phase closure `072aea7`
already recorded is retroactively valid** as of this correction.

**RESERVED ACTIVITIES PHASE CLOSED.** A1 (Special Activity backend),
A2 (Reserved Activity backend), B (frontend), and C (real acceptance)
are all now closed/passed. The shipped product boundary: a Special
Activity catalog; a fixed Reserved Activity aggregate (one Special
Activity, 1+ Classes, an optional single Teacher, explicit
instructional slots); hard Teacher-UNAVAILABLE rejection with
non-blocking PREFER_NOT; cross-`ReservedBlock` class/teacher collision
safety; fixed, zero-CP-SAT-variable solver occupancy; correct Class/
Teacher timetable projection of both teacher-attached and teacherless
reservations; configuration-lock enforcement identically across both
management surfaces and the raw API; and a responsive (desktop
matrix/mobile per-Day), accessible frontend workflow. Explicitly
deferred, still out of scope: Resources, ParticipantGroups/subgroups in
Reserved Activities, multiple Teachers per block, recurrence, duration
semantics, flexible/autoplaced special activities, and any
`ReservedBlock` soft solver scoring.

## RESOURCES A -- RESOURCE CATALOG BACKEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `6a90a28` ("feat: add
resource catalog backend") -- fast-forward merged from
`feature/resource-catalog-backend` (base `976b646`) onto `main`, with a
separate docs closure commit recording this status. Not pushed to any
remote. This closes Slice A only -- **the overall Resources phase is
NOT closed**; see `docs/PROJECT_STATE.md`'s matching entry for the full
implementation record and next-slice pointer.

**Locked product decision, reused unchanged:** `Resource` is the
existing `domain.resources.Resource(id, name, capacity=1)` entity --
Slice A adds only the missing catalog write surface over it, never a
new Room/ResourceType/ResourceCategory entity and never a domain
redesign. `capacity` means "maximum number of simultaneous lesson/
resource occupations" (the pre-existing solver semantics), never
student-seat/room-headcount capacity -- that would be a different,
separately-named future field. A future frontend surface for this
catalog is expected to be labeled "Rooms & Resources" in the UI --
a presentation-layer naming decision only, not a domain/API rename.

**Preflight capacity gap -- discovered during Resources A
implementation review, resolved before closure.** `run_preflight()`
was confirmed to emit zero errors for a `Resource` with `capacity <= 0`
constructed directly (only `UNKNOWN_RESOURCE` was checked). Per the
original slice instruction this was first reported as an open question
rather than silently fixed inside the narrow backend-catalog slice; a
subsequent narrow corrective pre-closure pass then resolved it: a new,
independent, purely structural `_check_resource_capacity()` check was
added to `validation/preflight.py` (no `application`/SQLAlchemy/ORM/
FastAPI import), and `run_preflight()` now emits `ValidationError(code=
"INVALID_RESOURCE_CAPACITY", context={"resource_id", "capacity"})` for
every `Resource` with `capacity < 1`, one diagnostic per invalid
Resource in problem order. The layered defense is now proven complete:
write-time application validation (`resource_rules.validate_capacity()`,
unchanged), `SchedulingProblem` preflight (this fix), and the DB
`CheckConstraint` structural backstop (unchanged) are all three
independently verified. Zero solver or verifier changes were made or
needed.

**Corrected forward guidance, locked for any future Resources B:**
Reserved Activity resource integration must enforce **aggregate
Resource capacity** (mirroring `model_builder.py`'s existing HARD
constraint #10 semantics), never a pairwise
`RESERVED_BLOCK_RESOURCE_SLOT_COLLISION`-style two-block exclusivity
check -- a pairwise check would silently misbehave for any Resource
whose `capacity != 1`.

Delete-in-use blocker in this slice checks only
`TeachingRequirement.resource_id` (`RESOURCE_IN_USE`,
`referenced_by: ["TEACHING_REQUIREMENT"]`) -- not `RESERVED_BLOCK`,
since `ReservedBlock.resource_id` does not exist yet. Owner Decision
#39 remains NOT created. Zero frontend, solver, verifier, or migration
changes in this slice (including the pre-closure preflight correction).

**Final verified baselines (reconfirmed after the fast-forward merge to
`main`):** core 434 passed/5 deselected, `tests_web` 438 passed/zero
skips, frontend 403 passed/25 files/zero skips, build clean, Alembic
`cae76cba3c58`/one head/no drift/zero migration.

**RESOURCES A -- RESOURCE CATALOG BACKEND CLOSED ON MAIN** --
implementation commit `6a90a28`. The overall Resources phase remains
NOT closed. **Next slice: Resources B1 -- ordinary
`TeachingRequirement` fixed-resource assignment contract.**

## RESOURCES B1 -- ORDINARY FIXED-RESOURCE ASSIGNMENT CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `0a61339` ("feat: add
resource assignment to teaching requirements") -- fast-forward merged
from `feature/resource-assignment-b1` (base `830ce06`) onto `main`,
with a separate docs closure commit recording this status. Not pushed
to any remote. This closes Slice B1 only -- **the overall Resources
phase is NOT closed**; see `docs/PROJECT_STATE.md`'s matching entry for
the full implementation record and next-slice pointer.

**Locked product decision: Option A**, per the B1 recon's recommendation
(the smallest coherent MVP with no second write surface, no second
transaction per logical edit, and no second page). Teaching Assignment
`POST/PUT` gained `resource_id: str | null`. Full-replacement, not
PATCH: omitted or explicit `null` always mean "no fixed Resource" on
POST, and always **clear** any currently-assigned Resource on PUT --
there is no "leave the Resource unchanged" option, matching how the
other four fields on this same write already behave.

**The `resource_requirement` Advanced-disqualifier is removed.**
`teaching_assignment_rules.plain_reasons()` no longer treats a fixed
Resource as disqualifying on its own -- an otherwise-plain
`WHOLE_CLASS`/`FLEXIBLE` requirement carrying a `resource_requirement`
is now editable and deletable through the same narrow write service
that already handles the other four fields. Every other existing
Advanced reason is unchanged. This was a required, intentional
behavior change (not a bug): the original Teaching Assignments slice
predates Resources and had no way to express "no resource" versus "some
resource," so it conservatively treated any Resource as Advanced;
Resources B1 removes that conservatism now that the field is properly
editable.

**No solver, verifier, domain, or schema change** -- `Resource`,
`ResourceRequirement`, `TeachingRequirement.resource_requirement`, the
`teaching_requirement.resource_id` column, and the solver's/preflight's
resource-capacity handling were already fully wired by Resources Slice
A; B1 only adds the missing ordinary-write entrypoint for a field the
rest of the system already understood. The solver never chooses among
Resources in this contract -- exactly one, admin-picked, fixed Resource
per requirement, or none.

Resource reference validation reuses `resource_rules.find_resource`
(Resources Slice A) and the existing generic `UnknownReferenceError`
(`reference_kind: "resource"`) -- no new error class. Explicitly
deferred: Resource Availability; `ReservedBlock.resource_id` (Resources
B2); Owner Decision #39 remains absent -- the B1 recon found no genuine
unresolved product fork.

**Final verified baselines:** core 446 passed/5 deselected, `tests_web`
464 passed/zero skips, frontend 415 passed/25 files/zero skips, build
clean, Alembic `cae76cba3c58`/one head/no drift/zero migration.
Confirmed live via a real-browser pass against the existing
`teacher-crud-review-school` dataset: previously-stranded
resource-bearing rows became editable/deletable, and assign/clear/
reassign all round-tripped correctly.

**RESOURCES B1 -- ORDINARY FIXED-RESOURCE ASSIGNMENT CLOSED ON MAIN** --
implementation commit `0a61339`. The overall Resources phase remains
NOT closed. **Next slice: Resources B2 -- Reserved Activity Resource
integration using aggregate Resource capacity.**

## RESOURCES B2 -- RESERVED ACTIVITY RESOURCE INTEGRATION CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `e8a3aaf` -- fast-
forward merged from `feature/reserved-activity-resource-b2` (base
`4025376`) onto `main`, with a separate docs closure commit recording
this status. Not pushed to any remote. See `docs/PROJECT_STATE.md`'s
matching entry for the full implementation record.

**Locked contract:** `ReservedBlock` gained one optional
`resource_id: str | None`, exposed identically on the Reserved Activity
API, full-replacement like every other field. At most one fixed
Resource per block, never solver-selected. **One `ReservedBlock`
consumes exactly ONE capacity unit of its Resource per slot, regardless
of how many `class_sections` participate** -- this was the one rule
this slice had to get exactly right, and it is enforced by a single
shared index (`ProblemIndex.reserved_resource_usage`), never
duplicated per layer.

**Aggregate capacity, explicitly never pairwise collision**, proven at
all three layers that could plausibly enforce it: preflight (new
`RESERVED_RESOURCE_CAPACITY_EXCEEDED`, reserved-vs-reserved structural
check only, reused through the existing candidate-diff mechanism --
zero new validation architecture), the solver
(`_add_resource_capacity` now subtracts fixed reserved usage from
capacity before constraining ordinary lesson variables --
`ReservedBlock`s stay fixed input, never CP-SAT variables), and the
independent verifier (`_check_resource_capacity` needed **zero**
production change, since it already counts every final entry by
`resource_id` regardless of source).

**A genuine correctness defect was found and fixed in this same task,
per the task's own instruction not to open a separate gate cycle for
it:** the schedule *read-back* mapper
(`persistence/mappers.py::schedule_entry_to_domain`) was not updated
alongside the fresh-solve entry builder
(`scheduling/result_builder.py`), so a `RESERVED_BLOCK` entry's
`resource_id` was correctly populated when freshly solved but silently
dropped to `None` when a persisted schedule was reloaded (e.g. by
timetable projections). Found via a real `POST .../schedule/generate`
against a live, resource-assigned dataset during the required
functional check -- not by static review. Fixed with a one-line
addition to that mapper's `RESERVED_BLOCK` branch, and regression-
proven at both the repository round-trip level and the real-solver
Class/Teacher timetable API level (the existing broader round-trip
test could not have caught this, since the shipped fixture's own
`ReservedBlock`s never carry a Resource).

**Migration:** one narrow Alembic migration, `9fbec2126831`, adding a
nullable `reserved_block.resource_id` plus a composite FK mirroring
`teaching_requirement.resource_id`'s existing pattern exactly. Applied
to both the primary and `TEST_DATABASE_URL` databases; all pre-existing
`reserved_block` rows survived with `resource_id = NULL`.

Resource delete-in-use blocking now also detects `RESERVED_BLOCK`
references (`resource_rules.find_resource_references`, deterministic
`TEACHING_REQUIREMENT`-then-`RESERVED_BLOCK` order). Resource
Availability remains deferred; no eligible-Resource sets, categories,
or preferred-Resource concept exist; the solver never chooses among
Resources. Owner Decision #39 remains absent -- no genuine unresolved
product fork appeared.

**Final verified baselines:** core 479 passed/5 deselected, `tests_web`
486 passed/zero skips, frontend 428 passed/25 files/zero skips, build
clean, Alembic `9fbec2126831`/one head/no drift on both databases.
Confirmed live via a real-browser pass plus a real
`POST .../schedule/generate` against the existing
`teacher-crud-review-school` dataset: the aggregate cross-source
capacity invariant held throughout a genuine 160-entry generated
schedule, and this exact check is what surfaced the read-back mapper
defect fixed in this same task.

**RESOURCES B2 -- RESERVED ACTIVITY RESOURCE INTEGRATION CLOSED ON
MAIN** -- implementation commit `e8a3aaf`. **Resources MVP scope
(A + B1 + B2) is now functionally complete; formal closure of the
overall Resources phase is deliberately left to the next product step**
-- Resource Availability remains an explicitly deferred, not-yet-scoped
future concern, never a completeness blocker.

## RESOURCES C -- ROOMS & RESOURCES CATALOG FRONTEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `58e8b5c` -- fast-
forward merged from `feature/resources-catalog-frontend` (base
`ec8a238`) onto `main`, with a separate docs closure commit recording
this status. Not pushed to any remote. See `docs/PROJECT_STATE.md`'s
matching entry for the full implementation record. **Zero backend/
migration/solver/verifier/domain production changes** -- frontend only,
against the existing, unmodified Resources A API.

**Doc correction (forward-only):** the RESOURCES B2 entry above
described the *backend/scheduling/resource-assignment* behavior as
functionally complete -- it did not describe a user-facing way to
manage the Resource catalog itself. That distinction stands as written;
this entry does not rewrite it. Precisely: A + B1 + B2 completed the
backend/scheduling/resource-assignment behavior; Resources C closes the
missing user-facing catalog-management surface; Resource Availability
remains explicitly deferred and was never an MVP blocker.

**Locked UI contract:** `Resource` (domain/API, unchanged) is
user-facing "Rooms & Resources" -- a fifth tab on the existing School
Setup page, following the same self-contained-panel architecture as
Teachers/Classes/Subjects/Special Activities. Exposes only `name` and
`capacity` (never a natural/ordinal/surrogate ID); capacity is presented
as "maximum number of simultaneous uses" via a small, visually-distinct
tinted callout next to the field, never seat/headcount/solver language.
Create defaults capacity to 1; local validation (integer, >= 1) blocks
submission before any request, backend validation remains authoritative.
Create/update send the full `{name, capacity}` representation
(full-replacement, matching every other write endpoint). Delete reuses
the inline (non-modal) confirmation pattern; `RESOURCE_IN_USE` maps
`TEACHING_REQUIREMENT` -> "Teaching Assignments" and `RESERVED_BLOCK` ->
"Reserved Activities" (both, when both apply), never raw backend
vocabulary. Configuration lock reuses the existing shared
`configuration_locked` pattern verbatim -- no Resource-specific locking
was invented.

**One real, narrow-width-only defect was found and fixed in this same
task:** the five-tab `.setup-tablist` overflowed the page horizontally
once a fifth tab was added (a pre-existing flex-row-with-no-wrap
pattern that only became a problem at five tabs). Fixed by giving the
tablist its own contained horizontal scroll region at the existing
narrow-width media query, rather than letting it force the page wider.
Confirmed via direct DOM measurement inside a same-origin iframe probe
sized to 375px, since the sandboxed environment's window could not
itself be resized below its fixed display size.

**Real-browser result:** confirmed live against the existing, unlocked
`teacher-crud-review-school` dataset -- full create/edit/delete cycle
on a new "Science Lab" Resource (capacity 1 -> "Science Lab A"/capacity
2 -> deleted), then confirmed delete is blocked on the dataset's
existing "Indoor Gym" Resource, which is referenced by both a Teaching
Assignment and a Reserved Activity, with the friendly "both" message.
Dataset ends this task in the identical state it started (`.env.local`
restored; the shared fixture untouched).

**Final verified baselines:** core 479 passed/5 deselected (unchanged),
`tests_web` 486 passed/zero skips (unchanged), frontend 460 passed/27
files/zero skips (428 + 32 new), build clean, Alembic
`9fbec2126831`/one head/no drift, zero new migration. A transient,
order-dependent failure in an unrelated, untouched test file
(`TimetablePage.test.tsx`) was observed once during a full-suite run,
confirmed to pass in isolation and on repeated full-suite runs
immediately before and after -- treated as a pre-existing test-isolation
flake, not a regression from this slice.

**RESOURCES C -- ROOMS & RESOURCES CATALOG FRONTEND CLOSED ON MAIN** --
implementation commit `58e8b5c`.

**RESOURCES MVP PHASE CLOSED.** MVP shipped scope: (1) Resource catalog
backend CRUD, (2) Rooms & Resources catalog frontend CRUD, (3) ordinary
Teaching Assignment fixed Resource, (4) Reserved Activity fixed
Resource, (5) aggregate cross-source Resource capacity, (6) independent
verification, (7) configuration locking / generation race safety, (8)
timetable Resource display. **Explicitly deferred, not blockers:**
Resource Availability; eligible Resource sets; capabilities/categories;
preferred Resource; solver-selected Resources; seat/headcount capacity
semantics. Owner Decision #39 remains absent unless a genuine new
product fork appears.

## CALENDAR A -- CALENDAR & BELL SCHEDULE BACKEND CLOSED ON MAIN

**Status: CLOSED ON MAIN.** Implementation commit `a508023` ("feat: add
calendar and bell schedule backend") -- fast-forward merged from
`feature/calendar-backend` (base `695cf76`) onto `main`, with a separate
docs closure commit recording this status. Not pushed to any remote.
This closes Slice A (backend) only -- the frontend "Calendar & Bell
Schedule" tab is a deliberately separate future slice (Calendar B).

**Locked product decision, reused unchanged:** `Day`/`Period` remain the
existing `domain.calendar.Day`/`Period` entities -- this slice adds only
the missing catalog write surface over them (create/update/delete/
Up-Down-move for both), plus two new optional `Period.start_time`/
`end_time` fields. Both fields are display/admin-only metadata; the
solver, `validation.preflight`, and the verifier never read them. Either
both are set or neither is; when both are set, `start_time` must be
strictly before `end_time`. An arbitrary number of Days and Periods is
supported, with free-text names (trimmed, non-blank, exact-case-
sensitive-duplicate-blocked per Academic Year).

**Lunch/break representation, locked:** no explicit Lunch/Break row is
ever created. A break is represented purely as a clock-time gap plus a
structural block boundary -- `starts_new_block: bool` is the only public
write field; the client never sees or sends a raw `block_id`.
`domain.calendar.derive_starts_new_block` derives the public marker from
the internal `block_id` sequence on read; `recompute_block_ids`
deterministically rebuilds `block_id` for the whole ordered Period
sequence after every create/update/delete/move, so `block_id` values
stay purely internal, never persisted-and-trusted client input. The
first Period in the sequence always starts a new block regardless of
its own stored marker.

**`is_instructional` policy, locked:** every Calendar-A-created Period
is inserted `is_instructional=True`; the field is absent from the public
write contract entirely (`PeriodWriteRequest`/`PeriodFields` have no
such field) and is never reassigned on update, so a legacy fixture-
seeded `is_instructional=False` row survives every future Calendar A
write untouched. It remains readable (never a normal write toggle) on
the Calendar projection.

**TimePreference index-drift safety, locked (the one genuinely hazardous
area in this slice):** `TeachingRequirement.time_preferences` stores raw
`Period.index` integers (`preferred_periods`), never `Period.id`
references. Therefore: (1) Period reorder (`move`) is unconditionally
blocked whenever *any* `TimePreference` exists anywhere in the Academic
Year (`PeriodReorderBlockedError`, deliberately a blanket rule rather
than a precise reachability check, per this slice's own instruction);
(2) Period delete is blocked precisely -- unsafe iff the target's own
index is named by some `TimePreference`, or any referenced index is
strictly greater than the target's (since deleting it would shift every
later Period's index down by one, silently redirecting that preference)
-- implemented once in `calendar_rules.validate_period_delete` and
covered by dedicated exact-index/shift/safe-because-later tests in both
`tests/test_calendar_service.py` and `tests_web/test_calendar_repository.py`.
Appending a new Period is always safe (no existing index moves) and is
never subject to this check. No preference's stored indexes are ever
silently rewritten by any Calendar A write.

**Minimum-calendar and indexing invariants, enforced in three
independent layers (write-time `calendar_rules`, `validation.preflight`'s
new `_check_calendar_shape`, and the DB's own `UNIQUE(academic_year_id,
idx)` constraint as a structural backstop):** at least one Day and at
least one instructional Period must always remain; `Day.index`/
`Period.index` stay contiguous `0..N-1` with no duplicates or gaps after
every write. `preflight.run_preflight` now also reuses
`domain.calendar.clock_time_overlaps` (the exact same pure function
`calendar_rules.validate_period_clock_order` runs at write-time) so a
write-time rejection and a preflight rejection can never disagree.

**Configuration lock/race, reused verbatim:** `persistence.
calendar_repository`'s two adapters follow Owner Decision #36's exact
lock-acquire / reject-if-locked / reload-under-lock / validate /
commit-or-rollback discipline (`configuration_write_lock.py`, unchanged)
-- no new concurrency mechanism was invented. `idx` reassignment
(Day delete-reindex; Period create/update/delete/move) uses a two-pass,
negative-sentinel write so the `UNIQUE(academic_year_id, idx)`
constraint is never transiently violated mid-reassignment.

**API surface, matching `/config`'s existing wire conventions:** `GET
.../calendar` (never exposing raw `block_id`), `POST/PUT/DELETE
.../calendar/days[/{day_id}]` + `POST .../days/{day_id}/move`, and the
Period equivalents. Clock times are always `"HH:MM"` strings on the
wire (`api/serializer.py`'s `format_hhmm`/`parse_hhmm`), never seconds/
ISO values; an invalid format is a safe 422 (`INVALID_PERIOD`/
`INVALID_TIME_FORMAT`), never a raw 500. `GET .../config`'s existing
`PeriodResponse` gained the same two additive, nullable `start_time`/
`end_time` fields -- confirmed backward compatible (every pre-Calendar-A
consumer already ignores unknown fields; no existing `/config` test
needed to change).

**Migration, one new revision:** `e0f73eda567b` ("add period bell
times") adds nullable `period.start_time`/`period.end_time` (`TIME`),
revising `9fbec2126831` -- no competing head was ever created. All 71
pre-existing dev-database `period` rows were confirmed to survive the
upgrade with both columns `NULL`. Zero DB `CHECK` constraint enforces
the both-null-or-both-present pairing rule -- that remains an
application-layer invariant only (`calendar_rules.validate_time_pair`),
matching this slice's locked scope.

**Zero solver, verifier, or frontend production changes.** The Calendar
tab itself (`School Setup -> "Calendar & Bell Schedule"`) is explicitly
out of scope for this slice -- see Calendar B.

**Final verified baselines:** core 546 passed/5 deselected (+120 new:
+11 `tests/test_domain.py`, +9 `tests/test_preflight.py`, +47 new
`tests/test_calendar_service.py`; the remainder split across the two
`tests_web` files below), `tests_web` 539 passed/zero skips (+53 new: 18
`tests_web/test_calendar_repository.py` + 35
`tests_web/test_calendar_api.py`), frontend 460 passed/27 files/zero
skips (unchanged -- zero frontend changes in this slice), build clean,
Alembic `e0f73eda567b`/one head/no drift.

**CALENDAR A -- CALENDAR & BELL SCHEDULE BACKEND CLOSED ON MAIN** --
implementation commit `a508023`. **Calendar phase remains NOT closed.**
**Next slice: Calendar B -- School Setup "Calendar & Bell Schedule"
frontend.**

## CALENDAR B -- SCHOOL SETUP FRONTEND CLOSED ON MAIN

A sixth local tab, "Calendar & Bell Schedule", added to
`SchoolSetupPage` after "Rooms & Resources" -- same self-contained,
tab-is-page-state-only architecture as the other five (Teachers,
Classes, Subjects, Special Activities, Rooms & Resources); no new
top-level navigation item, no nested tab routes. `frontend/src/api/
calendar.ts` is a new, dedicated one-domain-per-module client (matching
`resources.ts`) over the exact, unmodified Calendar A HTTP contract --
`GET .../calendar`, `POST/PUT/DELETE .../calendar/days[/{day_id}]` +
`.../move`, and the Period equivalents. No second frontend-facing
Calendar API shape was invented; no generic CRUD framework was added.

**Two visually distinct sections share one panel
(`CalendarBellSchedulePanel.tsx`):** "Working Days" and "Bell Schedule",
each a bordered card with its own heading/intro text, its own `+ Add`
toolbar, and its own `setup-table`. Neither section ever renders a raw
`id`, `index`, or `block_id` -- Day rows show only name + actions; Period
rows show name, HH:MM start/end (`"—"` when null), a block-boundary
badge, and actions.

**Reorder is Up/Down only, no drag-and-drop, no numeric index field.**
Each move button's accessible name includes context (`"Move Monday up"`,
`"Move Period 4 down"`), matching the locked accessibility requirement
that arrow icons alone never carry the action's meaning. The first row's
Up and the last row's Down are locally disabled from the projection's
own array position -- no separate "is this the edge" round trip. Every
successful write (create/update/delete/move) is followed by a fresh
`GET .../calendar` call; the `move` endpoint's own already-recomputed
`CalendarProjectionResponse` response body is deliberately not trusted
for the re-render, keeping one single "authoritative refresh" code path
for all eight mutation kinds rather than a special-cased one for move.

**Block-boundary UX (`starts_new_block`, never `block_id`):** the first
Period in server order is always shown with a fixed "First period of the
day" badge and no way to change it (the checkbox is hidden entirely,
both when creating into an empty calendar and when editing the current
first Period) -- the request body forces `starts_new_block: true` in
both cases regardless of local state. A later Period shows either
"Starts a new block after a break" or "Continues previous block", with
an editable checkbox plus an explanatory callout ("Use this when a
longer break or lunch separates this period from the previous one...").
Lunch/break itself is never a special entity or "+ Add lunch" action --
exactly a clock-time gap between two Periods plus the next Period's
`starts_new_block=true`, matching Calendar A's own MVP representation.

**Legacy `is_instructional=False` Periods remain visible, never
convertible:** shown with a "Non-instructional" badge and the fixed
hint "This legacy period is not available for lesson scheduling."
`PeriodWriteRequest` has no `is_instructional` field at all -- neither
the create nor the edit form offers any instructional-state control, so
a legacy row's flag cannot be flipped from this UI by construction, not
just by convention.

**Error mapping, friendly and code-driven (never raw backend
vocabulary):** `DUPLICATE_DAY`/`DUPLICATE_PERIOD`, `DAY_IN_USE`/
`PERIOD_IN_USE` (resolving `referenced_by` codes -- including
`TIME_PREFERENCE` for the delete-time index-drift-safety case -- to
plain labels), `INVALID_DAY`/`INVALID_PERIOD` (resolving each
`ValidationError.code`, e.g. `NO_CALENDAR_DAYS` -> "At least one working
day is required.", `NO_INSTRUCTIONAL_PERIODS` -> "At least one lesson
period is required.", `PERIOD_CLOCK_TIME_OVERLAP` -> a plain overlap
message), and `PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES` -> the exact
locked wording ("Periods cannot be reordered while teaching assignments
contain time preferences."), with no force-reorder escape hatch offered
anywhere. `SCHEDULING_CONFIGURATION_LOCKED` reuses the same lock-race
banner pattern as `RoomsResourcesPanel` (refetch into the authoritative
locked state, no separate mechanism).

**Configuration lock and stale-authority discipline reused verbatim**
from `RoomsResourcesPanel`: locked hides no data, disables every
mutation control; a failed post-write refetch shows a stale banner with
Retry and blocks further mutation until a fresh GET succeeds.

**Responsive/accessible:** the existing `.setup-tablist` horizontal-
scroll behavior (Resources C) is unchanged and now carries six tabs;
each `Calendar & Bell Schedule` section collapses its padding at
narrow widths; Day/Period name, start time, end time, and the
starts-a-new-block checkbox all carry explicit `<label>`s.

**Zero backend, migration, solver, or verifier production changes** --
confirmed by an unchanged `git diff` under `src/school_timetable/`, one
unchanged Alembic head (`e0f73eda567b`), and unchanged core/`tests_web`
totals.

**New test coverage (91 new frontend tests, zero backend changes):** 25
`frontend/src/api/calendar.test.ts` (every endpoint's exact path/body,
HH:MM and null-time round-trip, `is_instructional`/`block_id` absent
from the write-request type, every structured error code), 39
`CalendarBellSchedulePanel.test.tsx` (Working Days and Bell Schedule
CRUD/reorder/lock/stale/legacy-period coverage), 2 new
`SchoolSetupPage.test.tsx` cases plus the existing tab-order/keyboard-
navigation/`requestedTab` cases updated for six tabs.

**Final verified baselines:** core 546 passed/5 deselected (unchanged),
`tests_web` 539 passed/zero skips (unchanged), frontend 526 passed/29
files/zero skips (+66 new: 25 + 39 above + 2 new `SchoolSetupPage`
cases), build clean, Alembic `e0f73eda567b`/one head/no drift/zero new
migration.

**Real-browser acceptance: BLOCKED, not performed.** The Claude in
Chrome extension could not be connected in this environment session
(reported not-connected on repeated retries with the user's
acknowledgement); the Day/Period CRUD-and-reorder flows, the lunch-gap
representation check, and the 375px narrow-viewport check described in
this slice's task were therefore never exercised in a real browser.
Automated frontend tests above cover the same behavior at the
component/API level, but this is not a substitute for the locked
real-browser acceptance step -- it should be completed in a follow-up
session once browser access is available, before this UI is considered
fully accepted.

**CALENDAR B -- SCHOOL SETUP FRONTEND CLOSED ON MAIN** -- implementation
commit `58dd405`.

## CALENDAR MVP PHASE CLOSED

Shipped Calendar MVP scope: (1) AcademicYear-scoped arbitrary Working
Days; (2) AcademicYear-scoped arbitrary instructional Periods; (3) Day
CRUD; (4) Period CRUD; (5) accessible Up/Down ordering; (6) optional
paired bell start/end times; (7) lunch/break via clock gap + block
boundary; (8) `starts_new_block` abstraction over internal `block_id`;
(9) TimePreference index-drift protection; (10) minimum-calendar
invariants; (11) configuration lock/generation-race protection; (12)
School Setup "Calendar & Bell Schedule" frontend; (13) real-browser
CRUD/reorder acceptance; (14) solver remains calendar-shape agnostic.

**(13) is the one item not actually satisfied** -- see Calendar B's own
"Real-browser acceptance: BLOCKED" note above. Every other item is
implemented, tested, and merged to `main`.

Explicitly deferred: explicit visible Lunch/Break rows; editable
non-instructional Period creation; calendar dates/holidays/exceptions;
rotating A/B weeks; multiple bell schedules by weekday; manual timetable
editing; migrating `TimePreference` away from raw period indexes.
Resource Availability remains separately deferred.

**Owner Decision #39 remains absent** -- no genuinely new fork appeared
in this slice; every choice above was already locked by Calendar A's own
contract or by an existing pattern (Resources C's tablist/table
conventions, Owner Decision #36's lock discipline).

## CALENDAR MVP REAL-BROWSER ACCEPTANCE COMPLETED

Forward-only follow-up to Calendar B's "Real-browser acceptance:
BLOCKED" note above -- that note was accurate for that session (the
Claude in Chrome extension never connected). Claude in Chrome remained
unavailable in this follow-up session too, so the human user completed
the walkthrough manually against the same identified review dataset
(`reserved-activity-review-school` / `ay-reserved-activity-review-2026`
-- unlocked, zero `TimePreference` rows, 3 Days, 5 Periods including
the legacy `is_instructional=false` "Lunch" row), operating a normal
browser against a locally restarted `127.0.0.1:5173` Vite dev server
proxying the existing `127.0.0.1:8000` backend, while this session
prepared the dataset/environment and verified results directly against
the database and API before and after.

User-verified, all PASS: Working Days create (Saturday) / rename
(Saturday Activities) / Move Up / Move Down / delete, with the original
3-day set restored; Bell Schedule create (Acceptance Period, 16:10-
16:50) / rename+retime (Acceptance Period Extended, 16:15-16:55) /
`starts_new_block` toggle-on (block/break boundary display updated) /
Move Up / Move Down / delete, with the original 5-period set restored;
local HH:MM validation (only-Start, only-End, equal, reversed all
rejected; valid Start<End accepted, no backend call for any invalid
case); the legacy Lunch row visible, labeled Non-instructional, with no
instructional-state toggle and no "Add lunch"/"Add break" control
anywhere; and the ~375px narrow layout (no page-level horizontal
overflow, tablist/Working Days/Bell Schedule/forms/time inputs/actions
all usable).

**Dataset restoration independently confirmed** (not merely taken on
the user's word) via direct DB query and a `GET .../calendar` call
after the session: exactly Monday/Tuesday/Wednesday at indexes 0-2, and
exactly Period 1-4 (`morning`/`afternoon` blocks, all times still
`NULL`) plus `Lunch` (`midday` block, `is_instructional=false`) at
indexes 0-4 -- byte-for-byte the same as the pre-session baseline this
session recorded. Zero `Schedule`/`ScheduleVersion`/`TimePreference`
rows exist for this Academic Year; `configuration_locked` remains
`false`. `frontend/.env.local` was restored to
`VITE_SCHOOL_ID=synthetic-school`/`VITE_ACADEMIC_YEAR_ID=ay-2026` and
Vite restarted against it.

**No Calendar production-code change was required or made** -- this
follow-up found zero defects. Focused frontend suites (`calendar.test.ts`,
`CalendarBellSchedulePanel.test.tsx`, `SchoolSetupPage.test.tsx`): 87
passed. Build clean. Alembic `e0f73eda567b`, one head, no drift --
unchanged. Full backend/`tests_web` regression was not rerun for this
docs-only follow-up (Calendar B's own baselines remain authoritative,
since no backend code changed).

**The previously outstanding acceptance gap is now satisfied. Calendar
B is fully accepted. CALENDAR MVP IS FULLY ACCEPTED.** Deferred scope
is unchanged from Calendar B's own list above. **Owner Decision #39
remains absent.**

## FULL TIMETABLE END-TO-END ACCEPTANCE COMPLETED

With Calendar MVP closed, the full generated-timetable product path was
independently proven end-to-end against the normal pilot dataset
(`synthetic-school` / `ay-2026`), which already carried a pre-existing
active schedule (Schedule id 2, active `ScheduleVersion` 1, created
2026-09-07) -- not a fresh generation.

**Active schedule metadata:** `solver_status OPTIMAL`,
`total_soft_penalty 0`, `wall_time_seconds 0.259`, 160 `ScheduleEntry`
rows, 0 locked occurrences.

**Automated proof (read-only, no mutation):** a one-off script loaded
the real `SchedulingProblem` (`SessionFactorySchedulingProblemRepository`)
and the real active `ScheduleVersion`
(`SqlAlchemyScheduleVersionRepository`) and ran the existing,
unmodified `verification.verifier.verify()` against them --
**`passed: True`, zero violations** across all 12 of its checks. This
was cross-checked, not merely trusted: exact-full 40/40 occupancy for
all four class sections (160 occupied class-slots total, confirmed
correct once split-group branches are counted as one occupancy unit --
matching the verifier's own `_occupancy_unit_key` logic, not a naive
per-`ScheduleEntry` count); zero teacher double-bookings across all 8
teachers; both `UNAVAILABLE` slots (Teacher Science, Friday P7/P8) and
the one `PREFER_NOT` slot (Teacher History, Tuesday P3) all correctly
empty in the schedule; both `ReservedBlock`s (Chess Club at Wed P8 for
8a+8b jointly, Robotics Club at Thu P8 for 9a+9b jointly) placed
exactly as configured with no ordinary-activity overlap; the Indoor Gym
resource (capacity 1) never exceeded simultaneous usage of 1; the
German/Russian split group (`split_lang_8a`) synchronized in lock-step
across its 3 shared slots with two different teachers and no collision;
the merged History requirement (9a+9b) scheduled once, in one slot, for
one teacher, covering both classes; `math_8a`'s REQUIRED and
`science_8b`'s PREFERRED double-lesson block patterns both formed
exactly as configured (including the PREFERRED double actually being
achieved, consistent with zero soft penalty). `GET .../schedule/active/
classes/{id}` (all four) and `.../teachers/{id}` (representative
sample: Math, Science, History) were also called directly and found
structurally consistent with the DB-derived counts above -- zero empty
cells for any class, zero multi-entry (collision) cells for any
teacher, and the same UNAVAILABLE/PREFER_NOT slots empty in the
projection too.

**Human real-browser proof:** the user opened `/timetable` and visually
confirmed all four classes (8a, 8b, 9a, 9b) fully filled Monday-Friday
with no visible collisions, Chess Club and Robotics Club appearing in
their expected joint slots, and readable activity/teacher/class names
with no unexpected raw IDs; the user additionally opened every teacher
timetable (not just the Math/Science/History sample the automated pass
called out) and confirmed all of them correct.

**No product defect was found anywhere in this pass.**

**Important distinction, preserved deliberately:** this closes
acceptance of the *existing, already-generated* schedule as proof the
current product works end-to-end -- it does **not** demonstrate a fresh
`POST .../schedule/generate` run, because `synthetic-school` already
has an active `Schedule` and Decision #31's locked "Generate is
initial-generation-only" contract correctly refuses a second one
(`409 SCHEDULE_ALREADY_EXISTS`). A fresh-generation demonstration
remains open, to be done later on a separate, dedicated clean dataset
-- never by deleting or resetting this known-good baseline.

**FULL TIMETABLE END-TO-END ACCEPTANCE CLOSED ON MAIN** -- docs-only,
zero production-code change.

**Next major product area: admin-facing manual timetable editing,
locking, and re-optimization.** Every prerequisite is now accepted:
School Setup catalogs, Teacher Availability, Teaching Assignments,
Reserved Activities, Rooms & Resources, Calendar MVP, and now the full
generated timetable itself (both machine-verified and human-reviewed).
Not implemented in this task.

## FRESH TIMETABLE GENERATION END-TO-END ACCEPTANCE COMPLETED

Closes the one gap the previous entry explicitly left open: a genuine
*initial* `POST .../schedule/generate` run, proven end-to-end from a
verifiably clean dataset through browser display, on a dedicated
review dataset -- `generation-review-school` / `ay-generation-review-2026`
-- seeded via the existing `tests_web/support/problem_writer` test-only
writer against a renamed copy of `build_valid_fixture()` (no production
code added to create it).

**Initial state, confirmed before generation:** 0 `Schedule`, 0
`ScheduleVersion`, 0 `ScheduleEntry`, 0 `LockedOccurrence`. Full
production preflight passed with zero errors against 5 days, 8
instructional periods, 4 class sections, 8 teachers, 25 teaching
requirements (including one REQUIRED-block, one PREFERRED-block, two
split-group, one merged-class requirement, and one fixed placement),
3 availability rows, 2 reserved blocks, 1 resource.

**Generation, through the real HTTP path** (backend restarted first
onto current `main`, since the running process had been serving stale
code): `POST .../schedule/generate` -> `HTTP 201`, `version_number: 1`,
`solver_status: OPTIMAL`, `total_soft_penalty: 0`, `is_active: true`.
Persisted: exactly 1 `Schedule`, 1 `ScheduleVersion` (`parent_version_id`
null), 160 `ScheduleEntry` rows, 0 `LockedOccurrence` rows.

**Independent proof, not merely trusted `solver_status`:** the real
verifier ran against the real persisted entries -- `passed: True`, zero
violations. Cross-checked directly: exact-full 40/40 occupancy on all
four classes; zero teacher collisions across all 8 teachers; both
`UNAVAILABLE` slots and the one `PREFER_NOT` slot correctly unscheduled;
both `ReservedBlock`s placed exactly as configured with their joint
classes; the one `Resource`'s capacity never exceeded; the split-group
pair synchronized in lock-step; the merged-class requirement scheduled
once, correctly covering both classes; the REQUIRED block's pattern
matched exactly.

**Projection proof:** `GET .../schedule/active/classes/{id}` for all
four classes and `.../teachers/{id}` for all eight teachers -- zero
empty class cells, zero teacher-collision cells, readable names
throughout, no raw persistence ID leaked as a label.

**Idempotency:** a second `POST .../schedule/generate` call ->
`HTTP 409 SCHEDULE_ALREADY_EXISTS`, zero new rows, active version
unchanged -- Decision #31's initial-generation-only contract proven
under real HTTP, not just at the repository layer.

**Human real-browser proof:** the user opened
`http://localhost:5173/timetable` (frontend temporarily retargeted via
the gitignored `frontend/.env.local`, restored afterward) and reviewed
every class and teacher timetable view, confirming everything displayed
correctly. No defect reported.

**No product defect was found anywhere in this pass; zero production
code was changed to perform it.**

**Two known-good datasets now coexist, deliberately never merged or
reset into each other:** `synthetic-school`/`ay-2026` remains the
previously-generated regression baseline (Schedule active_version_id 2,
still version 1, `OPTIMAL`, penalty 0, confirmed untouched by this
slice); `generation-review-school`/`ay-generation-review-2026` is now
the independently-proven *fresh-generation* baseline (Schedule
active_version_id 9, version 1, `OPTIMAL`, penalty 0) -- it already
carries its accepted `ScheduleVersion` 1 and is therefore no longer a
"clean-before-generation" dataset; it must not be regenerated or reset
merely to re-run this proof again.

**FRESH TIMETABLE GENERATION END-TO-END ACCEPTANCE CLOSED ON MAIN** --
docs-only, zero production-code change, no migration.

**Next major product area: admin-facing manual timetable editing
frontend.** Every backend capability it needs already exists and is
accepted: move/swap, lock, unlock, re-optimize, immutable
`ScheduleVersion` creation, stale-version protection, truthful
soft-penalty metadata, the independent-verifier gate, and REQUIRED-
block-safe move validation. Only the UI slice exposing these through
the timetable views remains. Not implemented in this task.

## MANUAL TIMETABLE EDITING MVP END-TO-END ACCEPTANCE COMPLETED

Closes the UI slice the previous entry left open, including the
move-target-preview slice implemented after it (`POST .../schedule/
active/move/preview`, reusing `validate_move` directly, zero persistence
writes -- see that task's own commit `1fa7aea` for the full technical
detail). This entry records the end-to-end human acceptance of the
whole manual-editing MVP, not just the preview addition.

**Accepted behavior:** class-view, click-based editing (no
drag-and-drop); Move/swap gated behind an explicit confirmation step;
server-authoritative constraint validation via `validate_move` (the
frontend never re-implements a scheduling rule); a valid Move creates a
new immutable `ScheduleVersion`, an invalid one creates none and
displays the specific backend rejection reason; Lock/Unlock, each
producing a new version while carrying the base version's own truthful
`solver_status`/`total_soft_penalty` forward unchanged; Re-optimize
behind its own confirmation, honoring every lock and persisting the
real solver's own metadata; stale-`base_version_number` protection
(`409 STALE_SCHEDULE_VERSION`) on every mutating command; the
independent verifier and the REQUIRED-block-safe move-validation fix
(this file's earlier manual-editing correction slice) sitting behind
every mutation unchanged; split-group logical-occurrence handling;
Reserved Activities remaining fixed and non-editable; automatic active-
version refresh after every mutation; and move-target preview coloring
every candidate cell -- BLUE source, GREEN + ✓ allowed, RED + ×
forbidden with an inspectable reason, neutral/gray while loading or on
failure, never defaulting an unevaluated cell to allowed.

**Human real-browser acceptance (explicit, performed by the user) on
`editing-review-school`/`ay-editing-review-2026`:**

1. Valid Move -- Class 8-A, Science moved/swapped successfully; active
   `ScheduleVersion` increased.
2. Invalid Move -- the fixed Art lesson was rejected correctly; the UI
   showed "This move isn't allowed." plus the fixed-placement reason;
   no invalid mutation occurred.
3. Lock -- the lesson showed a visible Locked state; version increased;
   Move was unavailable while locked.
4. Unlock -- the Locked state disappeared; Move became available again;
   version increased.
5. Re-optimize -- completed successfully; new active version created;
   no error.
6. Move target preview -- the selected/source cell displayed distinctly;
   valid targets displayed GREEN + ✓; forbidden targets displayed
   RED + ×; a forbidden target's reason was visible; an allowed target
   could proceed to confirmation; the user confirmed the UX works and
   is good.

**Technical proof (completed in the implementation task, re-confirmed
here):** the preview endpoint reuses the authoritative `validate_move`
and creates zero persistence writes; 549/549 frontend tests, 575/575
core backend tests, 568/568 `tests_web` integration tests, a clean
production build, Alembic head unchanged at `e0f73eda567b`, no
migration.

**Dataset roles, as they stand now:** `generation-review-school`/
`ay-generation-review-2026` remains the protected fresh-generation
baseline, untouched (`ScheduleVersion` 1, `OPTIMAL`, penalty 0).
`editing-review-school`/`ay-editing-review-2026` is the disposable
manual-editing acceptance dataset and now intentionally carries
multiple `ScheduleVersion` rows from real human editing acceptance --
expected, not a defect. `synthetic-school`/`ay-2026` was accidentally
mutated by real Move/Lock/Reoptimize UI actions during earlier human
editing review, before the disposable dataset existed, progressing from
its original version 1 to version 7 -- **it is reclassified here: it is
no longer the pristine version-1 regression baseline it previously
was.** No destructive repair was attempted in this closure task, and no
further mutation of it occurred during this task's own work (including
its automated tests, which run against isolated/rolled-back
transactions and never touch this dataset).

**THE ADMIN MANUAL TIMETABLE EDITING MVP IS NOW END-TO-END ACCEPTED.**

**MANUAL TIMETABLE EDITING MVP ACCEPTANCE CLOSED ON MAIN** -- docs-only,
zero production-code change, no migration.

**Next major product area: schedule version history + restore.** Show
the immutable `ScheduleVersion` history for a school/year (version
number, timestamp, solver status, penalty), distinguish the current
active version, let the admin inspect an older version, and let them
safely restore one. Restoring must never mutate an old immutable
`ScheduleVersion` in place: the intended semantics are select a
historical version -> create a NEW immutable `ScheduleVersion` copied
from it -> parent it from the currently-active version -> promote the
new version active, preserving append-only history and giving a safe
Undo/Restore behavior. Not implemented in this task.

## SCHEDULE VERSION HISTORY + RESTORE MVP END-TO-END ACCEPTANCE COMPLETED

Closes the design slice the previous entry left open, implemented in
commit `7fa6241` ("feat: add schedule version history and restore").

**Accepted behavior:** immutable `ScheduleVersion` history listed
newest-first with a clear active-version indicator; historical class
and teacher timetable inspection at any specific past version; strictly
read-only historical mode (Move/Lock/Unlock/Re-optimize never offered
against a past version); a restore confirmation that explicitly states
a NEW version will be created (never phrased to suggest data loss);
Restore creates a NEW immutable `ScheduleVersion` -- the historical
source is never reactivated or mutated; the restored version's entries
and locked occurrences come exactly from the historical source, never
re-derived and never inherited from whatever was active immediately
before restoring; the new version's parent is that previously-active
version; the same `409 STALE_SCHEDULE_VERSION` protection every other
mutating editing command already uses; the independent verifier
re-checks the historical source against the current configuration
before persisting (defense-in-depth); every prior version is preserved
untouched -- restoring is strictly additive, never destructive.

**Human real-browser acceptance (explicit, performed by the user) on
`editing-review-school`/`ay-editing-review-2026`:** Version History
opened correctly; Version 9 was shown as ACTIVE; Version 1 opened in
historical read-only mode, clearly labeled "Viewing historical Version
1 — read only"; Class and Teacher historical viewing both worked; every
editing action was correctly unavailable in historical mode; "Restore
this version" was offered with its confirmation shown; restoring
Version 1 while Version 9 was active created a NEW Version 10, which
became active; Version 1 remained untouched historical data; success
feedback was shown; the full history (Versions 1-9) remained preserved.

**Independent post-acceptance verification (read-only, no further
mutation performed):** 10 `ScheduleVersion` rows now exist for
`editing-review-school`, exactly one active (Version 10);
`parent_version_number` of Version 10 is 9; Version 10's `entries`
(160/160) and `locked_occurrences` (both empty) exactly equal Version
1's, loaded independently via `ScheduleVersionRepository.get_version`;
Versions 1-9 remain present and unchanged; the independent verifier
passed against Version 10's entries with zero violations.

**Concrete acceptance, stated plainly:** Version 1 restored while
Version 9 was active -> NEW Version 10 created -> Version 10 became
active -> Version 1 remained historical and immutable -> Versions 2-9
remained preserved.

**THE SCHEDULE VERSION HISTORY + RESTORE MVP IS NOW END-TO-END
ACCEPTED.**

**Dataset status:** `generation-review-school`/`ay-generation-review-2026`
remains untouched (`ScheduleVersion` 1, `OPTIMAL`, penalty 0).
`synthetic-school`/`ay-2026` remains at its already-reclassified state
(version 7), unchanged by this closure. `editing-review-school`/
`ay-editing-review-2026` now reflects the accepted restore (10
versions, Version 10 active) -- the dataset's expected, intentional
disposable-acceptance state, not a defect.

**SCHEDULE VERSION HISTORY + RESTORE MVP ACCEPTANCE CLOSED ON MAIN** --
docs-only, zero production-code change, no migration.

**Next major product area: safe configuration changes after schedule
generation.** The system currently locks scheduling configuration
outright once a `Schedule` exists (Add Teacher, Add Class, Add Subject,
Add Assignment, availability/resource changes, etc. all become
disabled) -- safe for data integrity, but real schools need to change
configuration after a timetable has already been generated. The next
product/design slice should define a safe workflow: configuration
locked -> admin explicitly chooses "Edit scheduling configuration" ->
the current timetable/history remain preserved -> configuration changes
are made under a controlled mode -> the existing active schedule
becomes clearly stale/out-of-date -> the admin must regenerate/
re-optimize against the new configuration -> no silent mutation of any
historical `ScheduleVersion`. Not implemented in this task.

## SAFE CONFIGURATION CHANGES -- SLICE A -- CONFIGURATION REVISION
FOUNDATION CLOSED ON MAIN

**Owner Decision #39 -- Safe Configuration Changes is built on a
first-class `ConfigurationRevision` model (Option C from the prior
architecture study), Slice A implements the schema/persistence
foundation only (LOCKED; IMPLEMENTED, REVIEWED, COMMITTED to `main` --
CLOSED. Not pushed.).**

Four owner decisions for the *overall* safe-configuration-changes
product area were locked by the prior (research-only) architecture
study and are recorded here as accepted but **not yet implemented**:
(1) stale-timetable UX = A -- a banner plus disabled editing, never a
blocking interstitial (the UI itself is deferred to a later slice); (2)
Discard Draft = B -- requires an explicit confirmation (the backend
lifecycle for discarding a draft is deferred); (3) draft-editing MVP
scope = A -- when implemented, editing must eventually cover every
`SchedulingProblem` entity (Teachers, Classes, Participant Groups,
Subjects/Activities, Teaching Assignments, Teacher Availability,
Resources, Reserved Activities, Calendar Days/Periods, Time
Preferences, block policies, split/merged config), never just
Teacher+Assignment; (4) incompatible locks during a future regeneration
= B -- identify affected locks, show the admin, require explicit
confirmation before continuing without them. None of the four is
implemented behaviorally in Slice A; Slice A exists to make them
possible to implement safely later without a schema rewrite.

**The architecture decision itself:** every `SchedulingProblem`
configuration table (all fifteen: `Day`, `Period`, `ClassSection`,
`ParticipantGroup`, `ParticipantGroupClassSection`, `Teacher`,
`TeacherAvailability`, `Activity`, `Resource`, `TeachingRequirement`,
`TimePreference`, `ReservedBlock`, `ReservedBlockClassSection`,
`ReservedBlockSlot`, `FixedPlacement`) and `ScheduleVersion` now carry a
non-nullable `configuration_revision_id`, referencing a new first-class
`ConfigurationRevision` row (surrogate PK, `academic_year_id`,
`revision_number` unique within the year, `status` DRAFT/PUBLISHED,
`created_at`). `AcademicYear` gained `published_revision_id`/
`draft_revision_id` (nullable, circular FKs via `use_alter=True`,
mirroring the existing `Schedule.active_version_id` pattern). `PUBLISHED`
means "this revision was finalized and is permanently immutable", NOT
"this is the one currently-active published revision" -- a year may
accumulate any number of historical PUBLISHED revisions over its
lifetime, with `published_revision_id` alone identifying which one is
currently authoritative. At most one DRAFT revision per year is
enforced by the database itself via a partial unique index -- not
application discipline alone; there is deliberately no equivalent
constraint on PUBLISHED. (**Correction:** the originally-applied
migration mistakenly also enforced at most one PUBLISHED revision per
year; corrected the same day by a follow-up migration -- see the
dedicated decision entry below, recorded before this was ever relied
upon by Slice B or later work.) Natural IDs remain the stable logical
identity of an
entity across revisions and stay unique *within* one revision, never
globally: two revisions of the same year may permanently contain rows
sharing the same natural ID (this is precisely what will let Slice B
clone a published revision's rows into a new draft under identical
natural IDs). Every composite FK between two config tables was widened
to include `configuration_revision_id` on both sides, so the database
structurally rejects a row in one revision referencing a row from a
different revision -- proven by a dedicated persistence test, not just
asserted. `ScheduleVersion.configuration_revision_id` (RESTRICT on
delete) means every version -- initial generation, manual edit,
lock/unlock, re-optimize, restore -- permanently and immutably
identifies the exact revision it was built against; manual editing and
same-revision restore never change it.

**Initial-setup / first-Generate semantics (the one behavioral change
this slice makes):** a new `AcademicYear` now always receives an
initial editable DRAFT revision transactionally at creation.
Configuration writes before the first successful Generate target this
draft, exactly as before from the caller's perspective. The FIRST
successful Generate, at final persist (inside the same short
transaction that already holds the Owner-Decision-#36 `AcademicYear`
row lock and re-verifies the solved configuration didn't change),
atomically publishes that exact draft, sets it as the year's
`published_revision`, clears `draft_revision`, and creates the first
`ScheduleVersion` referencing the now-published revision -- no
`ScheduleVersion` is ever created referencing a still-mutable revision.
A failed solve/comparison leaves the draft fully DRAFT and editable,
nothing persisted. `SchedulingProblemRepository` gained
`load_for_revision(school, year, revision_number)` (natural revision
number only, never a surrogate DB ID across any domain/API boundary);
historical and active class/teacher projection and manual-
editing/reoptimize now resolve a `ScheduleVersion`'s *own* revision
first, then load that exact revision's `SchedulingProblem` -- never
"whatever is currently published."

**Explicitly NOT implemented in Slice A** (all deferred to Slice B or
later, per this decision's own scope): the "Edit scheduling
configuration" action, forking a draft from a published revision,
discarding a draft, the stale-timetable banner, Regenerate-after-
config-change, and any incompatible-lock detection/confirmation UI.
Configuration write behavior is otherwise completely unchanged: writes
before a Schedule exists still succeed (now targeting the draft
internally); the existing `SCHEDULING_CONFIGURATION_LOCKED` behavior
after a Schedule exists is untouched. Zero frontend files changed.

**Migration `83434054f9d2`** (revises `e0f73eda567b`) backfills every
pre-existing `AcademicYear` with exactly one `ConfigurationRevision`
(`revision_number=1`), PUBLISHED if the year already has a `Schedule`
else DRAFT, and backfills every existing config row and
`ScheduleVersion` to it -- an exact historical backfill, no schedule
data rewritten, nothing deleted. Applied to the real local development
database (pre-migration `pg_dump` backup taken first) and independently
verified read-only: all 14 `AcademicYear` rows (3 tracked datasets plus
11 other local review/CRUD datasets) each landed with exactly one
correctly-classified revision and zero NULLs in any
`configuration_revision_id` column anywhere.
`generation-review-school`/`ay-generation-review-2026` (1
`ScheduleVersion`, active Version 1, OPTIMAL, penalty 0),
`editing-review-school`/`ay-editing-review-2026` (10 `ScheduleVersion`
rows, active Version 10, all reference revision 1, independent verifier
re-passed against active Version 10 with zero violations), and
`synthetic-school`/`ay-2026` (7 `ScheduleVersion` rows, active pointer
and history unchanged, all reference revision 1) all preserved exactly.

**Regression:** focused migration-safety tests green (`tests_web/
test_persistence_schema.py` 12/12, `tests_web/test_schedule_schema.py`
15/15); full `tests -m "not slow"` 593/593; full `tests_web` 592/592;
frontend `npm test -- --run` 572/572 (zero frontend files changed) and
`npm run build` clean; `alembic check` reports zero drift post-
migration.

**SAFE CONFIGURATION CHANGES -- SLICE A ACCEPTANCE CLOSED ON MAIN --
schema/persistence foundation only. No safe post-generation
configuration editing exists yet. Slice B (draft fork/discard
lifecycle, stale-timetable UI, "Edit scheduling configuration") is not
started.**

## SAFE CONFIGURATION CHANGES -- SLICE A CORRECTION -- PUBLISHED-HISTORY
INVARIANT FIXED

**Correction to Owner Decision #39 -- `ConfigurationRevision.status =
'PUBLISHED'` means "this revision was finalized and is permanently
immutable", never "this is the currently-active published revision"
(LOCKED; IMPLEMENTED, REVIEWED, COMMITTED to `main` -- CLOSED. Not
pushed.).**

Slice A's originally-applied migration (`83434054f9d2`) mistakenly
created a partial unique index enforcing at most one PUBLISHED
`ConfigurationRevision` per `AcademicYear`, alongside the correct
at-most-one-DRAFT index. That was an architectural contradiction: the
two-state lifecycle is `DRAFT -> PUBLISHED`, and a year must be able to
accumulate multiple historical PUBLISHED revisions over its lifetime
(R1, R2, R3, ... -- each one finalized by a successful
Generate/regeneration in turn, remaining permanently immutable
afterward). `AcademicYear.published_revision_id` alone identifies
*which* PUBLISHED revision is currently authoritative; moving that
pointer from an older PUBLISHED revision to a newer one must never
require, or imply, changing the older revision's `status` back to
DRAFT, or touching it in any way. The erroneous index would have
structurally blocked exactly the future regeneration flow Slice A was
built to support (Slice B/C publishing a second revision while the
first remains a valid historical reference for its own
`ScheduleVersion`s).

**Fix:** a follow-up migration, `398b05641152` (revises `83434054f9d2`,
does not edit the already-applied migration in place), drops only
`uq_configuration_revision_one_published`. The at-most-one-DRAFT
partial unique index (`uq_configuration_revision_one_draft`) and
`UNIQUE(academic_year_id, revision_number)` are both untouched and
remain correct. No data backfill was needed or performed -- every
already-migrated year already had at most one PUBLISHED revision
anyway; the migration only removes a constraint that would have wrongly
prevented a second one from ever being created. The ORM model
(`persistence/models.py`) and its docstring were corrected to match,
and a new persistence test
(`test_academic_year_may_accumulate_multiple_historical_published_revisions`)
proves one `AcademicYear` can hold three simultaneous PUBLISHED
revisions plus one DRAFT, that a second DRAFT is still rejected, that
`revision_number` stays unique within the year, and that moving
`published_revision_id` from an older to a newer PUBLISHED revision
never mutates the older revision's row. A second new test
(`test_future_regeneration_publish_transition_is_schema_valid`) proves
-- structurally only, no Slice B/C application code -- that the future
regeneration flow (R1 PUBLISHED -> open R2 DRAFT -> R2 becomes
PUBLISHED, `draft_revision_id` cleared -> R1 remains untouched and
PUBLISHED) is valid under this schema today.

**Regression:** focused schema tests green (`tests_web/
test_persistence_schema.py` 14/14, including the two new tests;
`tests_web/test_schedule_schema.py` 15/15); full `tests -m "not slow"`
593/593; full `tests_web` 594/594; frontend `npm test -- --run`
572/572 and `npm run build` clean (zero frontend files changed);
`alembic check` reports zero drift after applying `398b05641152` to
the local development database (pre-migration `pg_dump` backup taken
first). All three tracked datasets (`generation-review-school`,
`editing-review-school`, `synthetic-school`) and the eleven other local
review/CRUD datasets verified unchanged: same published-revision
numbers, same draft state (null), same `ScheduleVersion` counts and
active pointers as before this correction.

**SAFE CONFIGURATION CHANGES -- SLICE A CORRECTION CLOSED ON MAIN --
the published-history invariant is now correct. Slice B (draft
fork/discard lifecycle, stale-timetable UI, "Edit scheduling
configuration") is still not started.**

## SAFE CONFIGURATION CHANGES -- SLICE B -- CONFIGURATION DRAFT
LIFECYCLE IMPLEMENTED, REVIEWED, VERIFIED -- READY TO COMMIT

**Owner Decision #40 -- the draft configuration lifecycle is owned by a
single new port, `ConfigurationRevisionRepository`
(`get_state`/`begin_draft`/`discard_draft`), which replaces "does a
Schedule exist" as the authority for whether configuration is locked
(LOCKED; IMPLEMENTED, REVIEWED, VERIFIED -- READY TO COMMIT. Not yet
committed, not pushed.).**

`configuration_write_lock.reject_if_configuration_locked`'s condition
changed from "a `Schedule` exists" to "no draft `ConfigurationRevision`
is open." Before this slice the two conditions were identical (a
year's draft was cleared the instant its first Generate published it,
and nothing ever set one again); this slice's `begin_draft` reopens a
draft for a year that already has a `Schedule`, and configuration
writes must succeed again once it does, redirected to that draft,
never touching the immutable published revision. All 9 configuration
write services (Teacher, Class Section, Subject, Special Activity,
Resource, Calendar Day/Period, Teaching Assignment, Teacher
Availability, Reserved Activity) had their own redundant, service-level
"is it locked" fast precheck deleted outright rather than updated --
the repository under its `AcademicYear` row lock is the sole
authoritative source of truth, and a second, service-level draft-state
check would have been pure duplication of that same authority. All 9
Setup projection services (the read-only siblings of the write
services above) were updated identically: `configuration_locked` on
every projection view is now derived from
`ConfigurationRevisionRepository.get_state`, never from "does a
Schedule exist."

**Eager clone, all fifteen tables, full FK remapping.** `begin_draft`,
called on a published/no-draft year, creates a new
`ConfigurationRevision` (next `revision_number`, `status=DRAFT`) and
clones every one of the fifteen `SchedulingProblem` configuration
tables (`Day`, `Period`, `ClassSection`, `ParticipantGroup`,
`ParticipantGroupClassSection`, `Teacher`, `TeacherAvailability`,
`Activity`, `Resource`, `TeachingRequirement`, `TimePreference`,
`ReservedBlock`, `ReservedBlockClassSection`, `ReservedBlockSlot`,
`FixedPlacement`) from the published revision into it, in dependency
order (the nine tables another table's FK can reference first, then
the six leaf/join tables). Every one of the 21 intra-configuration FK
edges between them is remapped to the NEW draft's own rows via
old-surrogate-id -> new-surrogate-id maps built as each table is
cloned -- never a raw copy of a published-revision surrogate FK value
into a draft row. Natural IDs and every scalar field are preserved
byte-for-byte; a clone failure rolls back the entire new revision,
leaving `draft_revision_id` unset. Called again while a draft is
already open (or before the year's first Generate, when the initial
draft already exists), `begin_draft` is idempotent: it returns the
existing draft unchanged, with no re-clone and no second revision.

**Concurrency proof, real PostgreSQL, no monkeypatch.** The whole clone
runs under the same `AcademicYear` `SELECT ... FOR UPDATE` row lock
(Owner Decision #36) every configuration writer already uses, held
from before the first insert through the final `commit()`. Proven, not
merely asserted: two genuinely independent, real-committed database
sessions (never the SAVEPOINT-nested pattern used by every other test
in this repository, which pins a test to one shared transaction and
so cannot exhibit genuine lock contention) raced via
`threading.Barrier` to call `begin_draft` for the same school/year at
the same instant. Direct timing instrumentation of the identical
scenario showed the two threads' execution windows overlapping for
~87ms of a ~90ms total duration each -- one thread spent nearly its
entire runtime genuinely blocked on the row lock, not merely losing a
race by chance. Both threads converged on the identical draft state;
independent verification found exactly one DRAFT revision, exactly one
clone copy of every one of the fifteen tables (never duplicated), and
the published revision completely unchanged.

**Draft-first reads and writes.**
`SchedulingProblemRepository.load_by_school_and_year` now resolves the
year's open DRAFT first, its PUBLISHED revision otherwise -- flipped
from the Slice A behavior (published-first), a distinction invisible
until a second revision could ever coexist. Every Setup screen and
every configuration writer's own `validate` closure therefore see the
configuration actually being edited the moment a draft is open, never
the now-frozen published one. Proven end-to-end with a real HTTP
acceptance test: create a Teacher while a draft is open -- the new row
belongs to the draft revision (never the published one); the
published revision's own `Teacher` rows are verified byte-for-byte
unchanged both immediately after the write and again after the draft
is later discarded; `GET .../teachers` reflects the new teacher while
the draft is open, and reverts to exactly the pre-draft list -- the new
teacher gone, nothing else changed -- once the draft is discarded.

**Discard lifecycle and its guards.** `discard_draft` clears the draft
pointer and hard-deletes the draft `ConfigurationRevision` row
(cascading, via the existing composite FKs' `ondelete="CASCADE"`, to
every one of its fifteen tables' draft-scoped rows), restoring the
published-only state; the published revision and every `Schedule`/
`ScheduleVersion`/`ScheduleEntry`/`LockedOccurrence` row are completely
untouched. Guarded: `NoConfigurationDraftError` if no draft is
currently open; `InitialDraftCannotBeDiscardedError` if the year's only
revision is its initial pre-first-Generate draft (required for that
first Generate to ever succeed, so it must never be discardable); and,
defense-in-depth, a bare `RuntimeError` if a draft is somehow still
referenced by a `ScheduleVersion` -- Slice A's own invariant makes this
state unreachable through any legitimate call path (a `ScheduleVersion`
is only ever created atomically with publishing the exact revision it
references, never while that revision is still a mutable draft), but
it is not blocked by any DB constraint either, so it was proven by a
dedicated defense-in-depth test that deliberately constructs the row
shape via a direct, out-of-band mutation -- never by weakening any
constraint or going through the repository's own normal API. Every
rejected discard performs zero mutation, verified explicitly in each
case.

**Stale-timetable mutation guard (Owner Decision 1, now implemented).**
`ConfigurationRevisionState.timetable_out_of_date` is `True` exactly
when a `Schedule` exists AND a draft is open. `ScheduleEditingService`
gained a `ConfigurationRevisionRepository` dependency and one shared
`_reject_if_out_of_date` guard, called by `move`/`lock`/`unlock`/
`reoptimize`/`restore` -- every one of these five now rejects with the
new `ScheduleOutOfDateError` while a draft is open, with zero mutation
(`ScheduleVersion`/`LockedOccurrence` counts independently verified
unchanged after each rejected attempt, at both the fake-repository unit
level and the real-database HTTP level). `preview_move` deliberately
never calls this guard and remains callable throughout, exactly as
Owner Decision 1 requires -- it is read-only and persists nothing
regardless of staleness. Mutations succeed again immediately once the
draft is discarded: the guard reads live state on every call, never a
cached flag, proven by a test that opens a draft, discards it, and
performs a normal move immediately afterward with the same service
instance.

**A genuine production gap was found and fixed while proving this
guard, not assumed correct from the implementation alone.**
`ScheduleOutOfDateError` had been wired into `ScheduleEditingService`
but never actually caught by `api/schedule_routes.py`'s exception
handling for the five mutating routes -- it would have surfaced as an
uncaught `500 Internal Server Error` instead of the intended `409
SCHEDULE_OUT_OF_DATE`. Caught by writing the guard's own real-HTTP
integration tests before considering this slice done (no route in this
codebase had ever exercised the error at all until then); fixed the
same session by adding the missing `except ScheduleOutOfDateError`
handler (mirroring the existing `StaleScheduleVersionError` -> 409
pattern) to `restore_schedule_version`, `move_schedule_entry`,
`lock_schedule_occurrence`, `unlock_schedule_occurrence`, and
`reoptimize_schedule` -- deliberately not `preview_move`, which never
raises it. Reconfirmed with 7 new real-HTTP integration tests (5
rejection cases plus preview-still-allowed and recovery-after-discard)
and the pre-existing 33 tests in that file, all passing.

**New HTTP surface:** `GET/POST/DELETE /schools/{school_id}/years/
{year_id}/configuration/{state,draft}` (`api/configuration_revision_
routes.py`) -- read revision state, open a draft, discard a draft.
Response body: `{published_revision_number, draft_revision_number,
configuration_locked, timetable_out_of_date}`, never a persistence
surrogate ID. Depends on `ConfigurationRevisionRepository` directly
(the same pattern `api/config_routes.py`'s existing `GET /config` route
already uses for `SchedulingProblemRepository`) -- none of the three
routes has any application-level orchestration beyond what the
repository already does inside its own locked transaction, so no
separate `application/` service class wraps this port. New stable
error codes: `409 NO_CONFIGURATION_DRAFT`, `409
INITIAL_DRAFT_CANNOT_BE_DISCARDED`, `409 SCHEDULE_OUT_OF_DATE` (the
last shared with the five schedule-editing routes above).

**Historical published revisions remain valid.** Nothing in this
slice's own code ever creates or mutates a `PUBLISHED` row --
`begin_draft`/`discard_draft` only ever touch the DRAFT. The
multi-published-revision invariant this guarantee depends on was fixed
and tested in the Slice A correction immediately above; this slice's
diff cannot regress it, since it has no code path that writes to
`published_revision_id` at all (only reads it).

**No migration, zero frontend change.** Built entirely on Slice A's
existing schema; `alembic check` reports zero drift throughout, no new
migration was needed or created. Zero frontend files changed -- no
draft banner, no Discard button, no Regenerate button, no Out-of-date
banner exist yet; the new routes exist only so a later UI slice has
something to call.

**Regression, stated chronologically and accurately:** the baseline
below was captured in full BEFORE this closure's two small audit-
cleanup edits (clarifying one Protocol docstring, and strengthening one
test's exception-message assertion -- both non-functional, no
production behavior changed); only the one directly affected test file
was re-run after those two edits, and stayed green (13/13). Core
`tests -m "not slow"` 600/600 (5 deselected); full `tests_web` 627/627;
frontend `npm test` 572/572 and `npm run build` clean; `alembic
current`/`heads` both `398b05641152` (one head), `alembic check`
reports no new upgrade operations. All three tracked real-database
datasets (`generation-review-school`: 1 version, active v1, OPTIMAL,
penalty 0; `editing-review-school`: 10 versions, active v10;
`synthetic-school`: 7 versions, active v7 -- all still revision 1) and
the other 11 local datasets verified unchanged, zero mutation
performed. The isolated test database confirmed empty (0 schools, 0
years) after the full integration/concurrency run. A final pre-commit
diff audit (54 changed files: 30 production, 24 tests, 0 docs, 0
migration, 0 frontend) found no blocker and verified all 14 Slice B
acceptance items PASS with direct evidence.

**SAFE CONFIGURATION CHANGES -- SLICE B ACCEPTANCE VERIFIED, NOT YET
COMMITTED -- the draft configuration lifecycle (begin/clone/discard,
draft-first reads/writes, stale-timetable mutation guard) is complete
and fully verified in the working tree, pending commit. Safe
post-generation configuration EDITING is now possible end-to-end, for
every `SchedulingProblem` configuration entity, through the existing
CRUD APIs, while a draft is open. Regeneration after a configuration
edit (Slice C) is explicitly NOT started -- there is still no way to
re-solve a schedule against an edited draft and publish it as a new
revision; discarding a draft simply reverts to the unchanged, still-
  active published schedule.**

## SAFE CONFIGURATION CHANGES -- SLICE C -- SAFE REGENERATION BACKEND CLOSED

Regeneration is distinct from initial generation. `POST
/schools/{school_id}/years/{year_id}/schedule/active/regenerate` requires
an open draft and `base_version_number`; `/schedule/generate` remains
initial-generation-only. The internal concurrency token is the non-reused
ConfigurationRevision database row identity, loaded together with the
detached draft problem before the DB-free solve. Persistence requires that
identity and the exact problem content to remain current under
`AcademicYear SELECT ... FOR UPDATE`; the identity is never exposed in
HTTP.

Compatible historical locks are hard pins in the ordinary generation solve
and remain on the new version. Incompatible locks require the exact set of
natural-ID occurrence keys; only confirmed incompatible locks are removed.
REQUIRED patterns use order-independent block-size multiplicity, and only
resolved locked logical occurrences consume capacity. FixedPlacement pins
one lesson-period, not an entire requirement; broader joint feasibility
remains the solver's responsibility.

The atomic persistence transaction publishes the exact solved draft, clears
its pointer, creates/activates ScheduleVersion N+1 linked to the new
revision, writes entries and compatible locks, and leaves history immutable.
Failures preserve the previous active/published state and current draft.
Edited-version configuration ID lookups are scoped to the active/base
version's configuration revision. No migration was required. Verification:
655 core tests (5 deselected), 660 real-PostgreSQL web/persistence tests,
199 focused Slice-C/backend tests, and Alembic head `398b05641152` with no
new upgrade operations.
