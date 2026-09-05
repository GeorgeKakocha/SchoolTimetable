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
