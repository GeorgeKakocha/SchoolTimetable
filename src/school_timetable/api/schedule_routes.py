"""`GET /schools/{school_id}/years/{year_id}/schedule/active`,
`POST /schools/{school_id}/years/{year_id}/schedule/generate` (Phase
3A3.4, `docs/DECISIONS.md` #31's locked HTTP contract -- initial
generation only, unchanged by the route below),
`POST /schools/{school_id}/years/{year_id}/schedule/active/regenerate`
(Safe Configuration Changes, Slice C, Checkpoint 5: `GenerateSchedule
Service.regenerate()`'s thin HTTP wrapper, including the explicit
incompatible-lock confirmation round trip -- documented on the route
function itself, directly below `generate_schedule`),
`GET /schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}`
(Phase 3B.1, `docs/DECISIONS.md` #32's locked HTTP contract), and
`GET /schools/{school_id}/years/{year_id}/schedule/active/teachers/{teacher_id}`
(next product slice after Phase 3C.3, no new phase number -- the
sibling teacher-timetable projection, same architecture/error
conventions as the class-timetable route directly below it), and the
manual-timetable-editing backend slice's four thin commands --
`POST .../schedule/active/move`, `.../lock`, `.../unlock`, and
`.../reoptimize` -- plus one read-only query, `POST .../schedule/active/
move/preview` (reports every candidate destination slot's `validate_move`
outcome for a given source occurrence, without persisting anything) --
each a thin wrapper over `ScheduleEditingService`, documented in their
own section near the bottom of this file.

Also the schedule version history + restore slice: `GET .../schedule/
versions` (history list), `GET .../schedule/versions/{version_number}/
classes/{class_section_id}` and `.../teachers/{teacher_id}` (read-only
historical projections, reusing the exact same projection logic and
response shapes as the plain active-schedule routes above), and
`POST .../schedule/versions/{version_number}/restore` (creates a NEW
`ScheduleVersion` copied from a historical one and promotes it active --
never reactivates or mutates the historical version itself) -- documented
in their own section further below.

Every route depends only on `application/` Protocols/services
(`ScheduleVersionRepository`, `GenerateScheduleService`,
`ClassTimetableService`, `TeacherTimetableService`,
`ScheduleEditingService`), never on a concrete `persistence/` class --
the composition root wiring those concrete, session-factory-backed
adapters lives entirely in `api/dependencies.py`.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching `/config`'s existing convention exactly.

Error mapping (locked, no remaining owner decisions):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing `/config` body,
  unchanged, no `code` field.
- (GET only) no active `Schedule` yet (`get_active_schedule` returned
  `None`) -> 404, `{"detail": "Active schedule not found"}` -- a
  distinct state from the one above, still no `code` field, never
  translated to/treated as `SchedulingProblemNotFoundError`.
- `ScheduleAlreadyExistsError` -> 409, `{"code":
  "SCHEDULE_ALREADY_EXISTS", "detail": "..."}`.
- `ScheduleInfeasibleError` -> 409, `{"code": "SCHEDULE_INFEASIBLE",
  "detail": "..."}`.
- `ConfigurationChangedDuringGenerationError` (Owner Decision #36, Phase
  3C.2b) -> 409, `{"code": "CONFIGURATION_CHANGED_DURING_GENERATION",
  "detail": "..."}` -- a deliberate, retryable correctness condition (a
  Phase 3C.2 configuration write committed between this generation's
  load and its final lock-protected recheck; zero rows were persisted),
  never allowed to fall through as a generic 500.
- `InvalidSchedulingConfigurationError` -> 422, `{"code":
  "INVALID_CONFIGURATION", "detail": "...", "errors": [...]}` -- the
  validator's own diagnostics, in their original order.
- (`move`/`lock`/`unlock`/`reoptimize`/`restore` only, never
  `move/preview`) `ScheduleOutOfDateError` (Safe Configuration Changes,
  Slice B, Owner Decision 1) -> 409, `{"code": "SCHEDULE_OUT_OF_DATE",
  "detail": "..."}` -- a configuration draft is currently open
  alongside this year's `Schedule`; the active timetable is read-only
  until the draft is discarded or a future regeneration succeeds.

Deliberately NOT caught here, by design: `ScheduleGenerationError`,
`ScheduleVerificationFailedError`, `persistence.schedule_repository.
CorruptScheduleStateError`, or any other unexpected exception -- these
are internal defects, never a client-actionable outcome, and are left
to reach FastAPI's normal unhandled-exception (generic 500) behavior
rather than importing persistence-internal defect types into this
route merely to translate them.

The class-timetable projection route's error mapping (also locked, no
remaining owner decisions): `SchedulingProblemNotFoundError` -> 404
(same body as above); `ClassTimetableService.project` returning `None`
(no active `Schedule` yet) -> 404, `{"detail": "Active schedule not
found"}` -- the identical body the plain `.../schedule/active` route
already uses for the same underlying state; `ClassSectionNotFoundError`
-> 404, `{"detail": "Class section not found"}` -- a third, distinct,
still code-less 404. No new stable `code` is introduced for any of
these three. Any other unexpected exception (including a corrupt
persisted `Schedule` state) is, again, deliberately not caught here.

The teacher-timetable projection route's error mapping is the exact
sibling of the class-timetable route's own, one level narrower:
`SchedulingProblemNotFoundError` -> 404 (same body); no active
`Schedule` yet (`TeacherTimetableService.project` returning `None`) ->
404, `{"detail": "Active schedule not found"}` (the identical body,
again); `TeacherNotFoundError` -> 404, `{"detail": "Teacher not
found"}`. No new stable `code` is introduced here either -- no
structured error codes exist on the sibling class route, so none are
invented for this one. Any other unexpected exception (including a
malformed stored teacher/group/class reference inside an entry, a
genuine configuration defect) is, again, deliberately not caught here
-- never silently papered over with an invented display label.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import (
    get_class_timetable_service,
    get_generate_schedule_service,
    get_schedule_editing_service,
    get_schedule_version_repository,
    get_teacher_timetable_service,
)
from school_timetable.api.schemas import (
    ActiveScheduleResponse,
    ClassTimetableResponse,
    GenerateScheduleResponse,
    GenerationErrorResponse,
    IncompatibleLockResponse,
    IncompatibleLocksRequireConfirmationErrorResponse,
    InvalidConfigurationResponse,
    InvalidEditTargetErrorResponse,
    LockRequest,
    MoveNotAllowedErrorResponse,
    MovePreviewRequest,
    MovePreviewResponse,
    MovePreviewTargetResponse,
    MoveRequest,
    MoveViolationResponse,
    NoConfigurationDraftErrorResponse,
    RegenerateScheduleRequest,
    ReoptimizationInfeasibleErrorResponse,
    ReoptimizationInvalidInputErrorResponse,
    ReoptimizeRequest,
    RestoreVerificationFailedErrorResponse,
    RestoreVersionRequest,
    ScheduleOutOfDateErrorResponse,
    ScheduleVersionHistoryResponse,
    ScheduleVersionNotFoundErrorResponse,
    StaleScheduleVersionErrorResponse,
    TeacherTimetableResponse,
    UnlockRequest,
    VersionAlreadyActiveErrorResponse,
)
from school_timetable.api.serializer import (
    active_schedule_response_from_active_version,
    class_timetable_response_from_view,
    generate_response_from_active_version,
    schedule_version_history_response_from_summaries,
    teacher_timetable_response_from_view,
    validation_diagnostic_response_from_error,
)
from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.errors import (
    ClassSectionNotFoundError,
    ConfigurationChangedDuringGenerationError,
    IncompatibleLocksRequireConfirmationError,
    InvalidEditTargetError,
    InvalidSchedulingConfigurationError,
    MoveNotAllowedError,
    NoActiveScheduleError,
    NoConfigurationDraftError,
    ReoptimizationInfeasibleError,
    ReoptimizationInvalidInputError,
    RestoreVerificationFailedError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
    ScheduleOutOfDateError,
    ScheduleVersionNotFoundError,
    SchedulingProblemNotFoundError,
    StaleScheduleVersionError,
    TeacherNotFoundError,
    VersionAlreadyActiveError,
)
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.ports import ScheduleVersionRepository
from school_timetable.application.schedule_editing_service import ScheduleEditingService
from school_timetable.application.teacher_timetable_service import TeacherTimetableService
from school_timetable.domain.schedule import OccurrenceKey

router = APIRouter()


def _stale_version_response(exc: StaleScheduleVersionError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=StaleScheduleVersionErrorResponse(
            code="STALE_SCHEDULE_VERSION",
            detail=str(exc),
            expected_base_version_number=exc.expected_base_version_number,
            actual_active_version_number=exc.actual_active_version_number,
        ).model_dump(),
    )


def _out_of_date_response(exc: ScheduleOutOfDateError) -> JSONResponse:
    """Safe Configuration Changes, Slice B, Owner Decision 1: the
    stale-timetable mutation guard's stable HTTP contract -- every
    mutating editing command (`move`/`lock`/`unlock`/`reoptimize`/
    `restore`) maps `ScheduleEditingService`'s `ScheduleOutOfDateError`
    identically. `preview_move` never raises it (stays read-only and
    callable even while stale), so it has no handler for this error."""
    return JSONResponse(
        status_code=409,
        content=ScheduleOutOfDateErrorResponse(code="SCHEDULE_OUT_OF_DATE", detail=str(exc)).model_dump(),
    )


@router.get("/schools/{school_id}/years/{year_id}/schedule/active", response_model=ActiveScheduleResponse)
def get_active_schedule(
    school_id: str,
    year_id: str,
    repository: ScheduleVersionRepository = Depends(get_schedule_version_repository),
) -> ActiveScheduleResponse:
    try:
        active = repository.get_active_schedule(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    if active is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return active_schedule_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/generate",
    response_model=GenerateScheduleResponse,
    status_code=201,
)
def generate_schedule(
    school_id: str,
    year_id: str,
    service: GenerateScheduleService = Depends(get_generate_schedule_service),
):
    try:
        active = service.generate(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ScheduleAlreadyExistsError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="SCHEDULE_ALREADY_EXISTS",
                detail="A schedule already exists for this school and academic year",
            ).model_dump(),
        )
    except ScheduleInfeasibleError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="SCHEDULE_INFEASIBLE",
                detail="No feasible schedule exists for this school and academic year",
            ).model_dump(),
        )
    except ConfigurationChangedDuringGenerationError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="CONFIGURATION_CHANGED_DURING_GENERATION",
                detail="Scheduling configuration changed during generation; retry generation",
            ).model_dump(),
        )
    except InvalidSchedulingConfigurationError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidConfigurationResponse(
                code="INVALID_CONFIGURATION",
                detail="Scheduling configuration is invalid",
                errors=tuple(
                    validation_diagnostic_response_from_error(e) for e in exc.validation_errors
                ),
            ).model_dump(),
        )
    return generate_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/regenerate",
    response_model=ActiveScheduleResponse,
)
def regenerate_schedule(
    school_id: str,
    year_id: str,
    body: RegenerateScheduleRequest,
    service: GenerateScheduleService = Depends(get_generate_schedule_service),
):
    """Safe Configuration Changes, Slice C, Checkpoint 5:
    `GenerateScheduleService.regenerate()`'s thin HTTP wrapper --
    distinct from `POST .../schedule/generate` above, which remains
    initial-generation only and is unchanged by this route's addition.
    `confirmed_incompatible_lock_keys` is deserialized into exactly the
    `frozenset[OccurrenceKey]` the service expects; an empty request
    default is valid exactly when nothing is actually incompatible --
    `regenerate()` itself is the sole authority on whether that is true.

    Error mapping (mirrors `generate_schedule`'s own, plus the manual-
    editing routes' `NoActiveScheduleError`/`StaleScheduleVersionError`
    conventions, plus one new one):
    - `SchedulingProblemNotFoundError` -> 404 (same body as every other
      route in this file).
    - `NoActiveScheduleError` -> 404, `{"detail": "Active schedule not
      found"}` -- the identical code-less body every mutating editing
      command already uses; regeneration is never the way a *first*
      `ScheduleVersion` is created.
    - `StaleScheduleVersionError` -> 409, the same `STALE_SCHEDULE_VERSION`
      contract every mutating editing command uses (`_stale_version_
      response`).
    - `NoConfigurationDraftError` -> 409, `{"code":
      "NO_CONFIGURATION_DRAFT", "detail": "..."}` -- the exact same
      contract `DELETE .../configuration/draft` already uses.
    - `IncompatibleLocksRequireConfirmationError` -> 409, `{"code":
      "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION", "detail": "...",
      "incompatible_locks": [{"requirement_id", "day_id",
      "anchor_period_id", "reason_code", "message"}, ...]}` -- the FRESH
      classification the exception carries, never a stale one; the
      caller must echo these exact keys back as a retried request's
      `confirmed_incompatible_lock_keys` to proceed.
    - `ScheduleInfeasibleError` -> 409, the same `SCHEDULE_INFEASIBLE`
      `GenerationErrorResponse` contract `generate_schedule` uses.
    - `ConfigurationChangedDuringGenerationError` -> 409, the same
      `CONFIGURATION_CHANGED_DURING_GENERATION` `GenerationErrorResponse`
      contract `generate_schedule` uses.
    - `InvalidSchedulingConfigurationError` -> 422, the same
      `InvalidConfigurationResponse` contract `generate_schedule` uses.
    - Deliberately NOT caught here, exactly like `generate_schedule`:
      `ScheduleGenerationError`, `ScheduleVerificationFailedError`, or any
      other unexpected exception -- internal defects, left to FastAPI's
      normal unhandled-exception (generic 500) behavior.
    - A successful regeneration returns the exact same
      `ActiveScheduleResponse` shape every other mutating command does,
      for its newly-active version.
    """
    confirmed_incompatible_lock_keys = frozenset(
        OccurrenceKey(k.requirement_id, k.day_id, k.anchor_period_id)
        for k in body.confirmed_incompatible_lock_keys
    )
    try:
        active = service.regenerate(
            school_id, year_id, body.base_version_number, confirmed_incompatible_lock_keys,
        )
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except NoConfigurationDraftError as exc:
        return JSONResponse(
            status_code=409,
            content=NoConfigurationDraftErrorResponse(
                code="NO_CONFIGURATION_DRAFT", detail=str(exc),
            ).model_dump(),
        )
    except IncompatibleLocksRequireConfirmationError as exc:
        return JSONResponse(
            status_code=409,
            content=IncompatibleLocksRequireConfirmationErrorResponse(
                code="INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION",
                detail=str(exc),
                incompatible_locks=tuple(
                    IncompatibleLockResponse(
                        requirement_id=lock.key.requirement_id,
                        day_id=lock.key.day_id,
                        anchor_period_id=lock.key.anchor_period_id,
                        reason_code=lock.reason_code,
                        message=lock.message,
                    )
                    for lock in exc.incompatible_locks
                ),
            ).model_dump(),
        )
    except ScheduleInfeasibleError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="SCHEDULE_INFEASIBLE",
                detail="No feasible schedule exists for this school and academic year",
            ).model_dump(),
        )
    except ConfigurationChangedDuringGenerationError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="CONFIGURATION_CHANGED_DURING_GENERATION",
                detail="Scheduling configuration changed during generation; retry generation",
            ).model_dump(),
        )
    except InvalidSchedulingConfigurationError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidConfigurationResponse(
                code="INVALID_CONFIGURATION",
                detail="Scheduling configuration is invalid",
                errors=tuple(
                    validation_diagnostic_response_from_error(e) for e in exc.validation_errors
                ),
            ).model_dump(),
        )
    return active_schedule_response_from_active_version(active)


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}",
    response_model=ClassTimetableResponse,
)
def get_class_timetable(
    school_id: str,
    year_id: str,
    class_section_id: str,
    service: ClassTimetableService = Depends(get_class_timetable_service),
) -> ClassTimetableResponse:
    try:
        view = service.project(school_id, year_id, class_section_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    if view is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return class_timetable_response_from_view(view)


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/active/teachers/{teacher_id}",
    response_model=TeacherTimetableResponse,
)
def get_teacher_timetable(
    school_id: str,
    year_id: str,
    teacher_id: str,
    service: TeacherTimetableService = Depends(get_teacher_timetable_service),
) -> TeacherTimetableResponse:
    try:
        view = service.project(school_id, year_id, teacher_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeacherNotFoundError:
        raise HTTPException(status_code=404, detail="Teacher not found") from None
    if view is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return teacher_timetable_response_from_view(view)


# -- Schedule version history + restore ------------------------------------
#
# `GET .../schedule/versions` lists every `ScheduleVersion` newest-first
# (metadata only). `GET .../schedule/versions/{version_number}/classes/
# {class_section_id}` and `.../teachers/{teacher_id}` are historical
# siblings of the plain active-schedule projections above -- they reuse
# `ClassTimetableResponse`/`TeacherTimetableResponse` unchanged, `is_active`
# now possibly `false`. `POST .../schedule/versions/{version_number}/
# restore` reuses `ActiveScheduleResponse` unchanged too.
#
# Error mapping, shared by all four routes below:
# - `SchedulingProblemNotFoundError` -> 404 (same body as every other
#   route in this file).
# - `list_versions`/`project_version`/(restore's `_load_active_for_edit`)
#   finding no `Schedule` at all -> 404, `{"detail": "Active schedule not
#   found"}` -- the identical code-less body every other route in this
#   file already uses for the same underlying state.
# - (historical class projection only) `ClassSectionNotFoundError` -> 404,
#   `{"detail": "Class section not found"}`.
# - (historical teacher projection only) `TeacherNotFoundError` -> 404,
#   `{"detail": "Teacher not found"}`.
# - (historical class/teacher projection and restore) `ScheduleVersionNotFoundError`
#   -> 404, `{"code": "SCHEDULE_VERSION_NOT_FOUND", "detail": "...",
#   "version_number": ...}` -- the requested `version_number` does not
#   exist for this school/year's `Schedule`; never silently falls back
#   to the active version.
# - (restore only) `StaleScheduleVersionError` -> 409, the same
#   `STALE_SCHEDULE_VERSION` contract every mutating editing command uses.
# - (restore only) `VersionAlreadyActiveError` -> 409, `{"code":
#   "VERSION_ALREADY_ACTIVE", "detail": "...", "version_number": ...}` --
#   the requested restore source is already the active version.
# - (restore only) `RestoreVerificationFailedError` -> 409, `{"code":
#   "RESTORE_VERIFICATION_FAILED", "detail": "...", "version_number":
#   ...}` -- defense-in-depth only, expected never to occur in ordinary
#   operation.
# - A successful restore returns the exact same `ActiveScheduleResponse`
#   shape every other mutating editing command does, for its newly-active
#   version (a fresh copy of the historical source, never the historical
#   version itself reactivated).


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/versions",
    response_model=ScheduleVersionHistoryResponse,
)
def get_schedule_version_history(
    school_id: str,
    year_id: str,
    repository: ScheduleVersionRepository = Depends(get_schedule_version_repository),
) -> ScheduleVersionHistoryResponse:
    try:
        summaries = repository.list_versions(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    if summaries is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return schedule_version_history_response_from_summaries(summaries)


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/versions/{version_number}/classes/{class_section_id}",
    response_model=ClassTimetableResponse,
)
def get_class_timetable_for_version(
    school_id: str,
    year_id: str,
    version_number: int,
    class_section_id: str,
    service: ClassTimetableService = Depends(get_class_timetable_service),
) -> ClassTimetableResponse:
    try:
        view = service.project_version(school_id, year_id, class_section_id, version_number)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    except ScheduleVersionNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ScheduleVersionNotFoundErrorResponse(
                code="SCHEDULE_VERSION_NOT_FOUND", detail=str(exc), version_number=version_number,
            ).model_dump(),
        )
    if view is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return class_timetable_response_from_view(view)


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/versions/{version_number}/teachers/{teacher_id}",
    response_model=TeacherTimetableResponse,
)
def get_teacher_timetable_for_version(
    school_id: str,
    year_id: str,
    version_number: int,
    teacher_id: str,
    service: TeacherTimetableService = Depends(get_teacher_timetable_service),
) -> TeacherTimetableResponse:
    try:
        view = service.project_version(school_id, year_id, teacher_id, version_number)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeacherNotFoundError:
        raise HTTPException(status_code=404, detail="Teacher not found") from None
    except ScheduleVersionNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ScheduleVersionNotFoundErrorResponse(
                code="SCHEDULE_VERSION_NOT_FOUND", detail=str(exc), version_number=version_number,
            ).model_dump(),
        )
    if view is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return teacher_timetable_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/versions/{version_number}/restore",
    response_model=ActiveScheduleResponse,
)
def restore_schedule_version(
    school_id: str,
    year_id: str,
    version_number: int,
    body: RestoreVersionRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    try:
        active = service.restore(school_id, year_id, body.base_version_number, version_number)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except ScheduleOutOfDateError as exc:
        return _out_of_date_response(exc)
    except VersionAlreadyActiveError as exc:
        return JSONResponse(
            status_code=409,
            content=VersionAlreadyActiveErrorResponse(
                code="VERSION_ALREADY_ACTIVE", detail=str(exc), version_number=version_number,
            ).model_dump(),
        )
    except ScheduleVersionNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=ScheduleVersionNotFoundErrorResponse(
                code="SCHEDULE_VERSION_NOT_FOUND", detail=str(exc), version_number=version_number,
            ).model_dump(),
        )
    except RestoreVerificationFailedError as exc:
        return JSONResponse(
            status_code=409,
            content=RestoreVerificationFailedErrorResponse(
                code="RESTORE_VERIFICATION_FAILED", detail=str(exc), version_number=version_number,
            ).model_dump(),
        )
    return active_schedule_response_from_active_version(active)


# -- Manual timetable editing (ScheduleEditingService) --------------------
#
# Error mapping, shared by all five routes below:
# - `SchedulingProblemNotFoundError` -> 404 (same body as every other
#   route in this file).
# - `NoActiveScheduleError` -> 404, `{"detail": "Active schedule not
#   found"}` -- the identical code-less body `GET .../schedule/active`
#   already uses for the same underlying state.
# - `StaleScheduleVersionError` -> 409, `{"code":
#   "STALE_SCHEDULE_VERSION", "detail": "...", "expected_base_version_
#   number": ..., "actual_active_version_number": ...}`.
# - (move only) `MoveNotAllowedError` -> 409, `{"code":
#   "MOVE_NOT_ALLOWED", "detail": "...", "violations": [{"code": ...,
#   "message": ...}, ...]}` -- every `MoveViolation`, in order, never
#   flattened to a generic message.
# - (move/preview/lock/unlock only) `InvalidEditTargetError` -> 422,
#   `{"code": "INVALID_EDIT_TARGET", "detail": "..."}`.
# - (reoptimize only) `ReoptimizationInfeasibleError` -> 409, `{"code":
#   "REOPTIMIZATION_INFEASIBLE", "detail": "..."}`; `ReoptimizationInvalid
#   InputError` -> 422, `{"code": "REOPTIMIZATION_INVALID_INPUT",
#   "detail": "...", "errors": [...]}` (the same diagnostic shape
#   `InvalidConfigurationResponse` already uses).
# - Every successful mutating command returns the exact same
#   `ActiveScheduleResponse` shape `GET .../schedule/active` does, for its
#   newly-active version -- no second GET is ever required to refresh the
#   projection.
# - `POST .../schedule/active/move/preview` is the one read-only route in
#   this group: zero persistence, no `ScheduleVersion` is ever created by
#   it. It never raises `MoveNotAllowedError` -- instead it reports, for
#   every OTHER instructional (day, period) slot, exactly what
#   `validate_move` would say about moving the given source occurrence
#   there, as `MovePreviewResponse{version_number, targets: [{day_id,
#   period_id, allowed, violations: [{code, message}]}]}`. An unresolvable
#   source still maps to `InvalidEditTargetError` -> 422, same as above --
#   never a fake all-red/all-green grid.
# - Deliberately NOT caught, by design (an internal defect, left to
#   FastAPI's normal unhandled-exception/generic-500 behavior): `schedule_
#   editing_service.EditVerificationFailedError`/`ReoptimizationError`,
#   and any other unexpected exception.


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/move",
    response_model=ActiveScheduleResponse,
)
def move_schedule_entry(
    school_id: str,
    year_id: str,
    body: MoveRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    try:
        active = service.move(
            school_id, year_id, body.base_version_number,
            body.requirement_id, body.source_day_id, body.source_period_id,
            body.target_day_id, body.target_period_id,
        )
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except ScheduleOutOfDateError as exc:
        return _out_of_date_response(exc)
    except MoveNotAllowedError as exc:
        return JSONResponse(
            status_code=409,
            content=MoveNotAllowedErrorResponse(
                code="MOVE_NOT_ALLOWED",
                detail=str(exc),
                violations=tuple(MoveViolationResponse(code=c, message=m) for c, m in exc.violations),
            ).model_dump(),
        )
    except InvalidEditTargetError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidEditTargetErrorResponse(code="INVALID_EDIT_TARGET", detail=str(exc)).model_dump(),
        )
    return active_schedule_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/move/preview",
    response_model=MovePreviewResponse,
)
def preview_move(
    school_id: str,
    year_id: str,
    body: MovePreviewRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    """Read-only: never persists, never creates a `ScheduleVersion`.
    Reports the exact same `validate_move` outcome the real move command
    would compute for every other instructional slot -- error mapping is
    otherwise identical to `move_schedule_entry` above (`MoveNotAllowedError`
    is never raised here; a per-target rejection is reported inline as
    `allowed: false` plus its violations, not an HTTP error)."""
    try:
        preview = service.preview_move(
            school_id, year_id, body.base_version_number,
            body.requirement_id, body.source_day_id, body.source_period_id,
        )
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except InvalidEditTargetError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidEditTargetErrorResponse(code="INVALID_EDIT_TARGET", detail=str(exc)).model_dump(),
        )
    return MovePreviewResponse(
        version_number=preview.version_number,
        targets=tuple(
            MovePreviewTargetResponse(
                day_id=t.day_id,
                period_id=t.period_id,
                allowed=t.allowed,
                violations=tuple(MoveViolationResponse(code=c, message=m) for c, m in t.violations),
            )
            for t in preview.targets
        ),
    )


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/lock",
    response_model=ActiveScheduleResponse,
)
def lock_schedule_occurrence(
    school_id: str,
    year_id: str,
    body: LockRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    try:
        active = service.lock(
            school_id, year_id, body.base_version_number,
            body.requirement_id, body.day_id, body.period_id,
        )
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except ScheduleOutOfDateError as exc:
        return _out_of_date_response(exc)
    except InvalidEditTargetError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidEditTargetErrorResponse(code="INVALID_EDIT_TARGET", detail=str(exc)).model_dump(),
        )
    return active_schedule_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/unlock",
    response_model=ActiveScheduleResponse,
)
def unlock_schedule_occurrence(
    school_id: str,
    year_id: str,
    body: UnlockRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    try:
        active = service.unlock(
            school_id, year_id, body.base_version_number,
            body.requirement_id, body.day_id, body.period_id,
        )
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except ScheduleOutOfDateError as exc:
        return _out_of_date_response(exc)
    except InvalidEditTargetError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidEditTargetErrorResponse(code="INVALID_EDIT_TARGET", detail=str(exc)).model_dump(),
        )
    return active_schedule_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/active/reoptimize",
    response_model=ActiveScheduleResponse,
)
def reoptimize_schedule(
    school_id: str,
    year_id: str,
    body: ReoptimizeRequest,
    service: ScheduleEditingService = Depends(get_schedule_editing_service),
):
    try:
        active = service.reoptimize(school_id, year_id, body.base_version_number)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoActiveScheduleError:
        raise HTTPException(status_code=404, detail="Active schedule not found") from None
    except StaleScheduleVersionError as exc:
        return _stale_version_response(exc)
    except ScheduleOutOfDateError as exc:
        return _out_of_date_response(exc)
    except ReoptimizationInfeasibleError:
        return JSONResponse(
            status_code=409,
            content=ReoptimizationInfeasibleErrorResponse(
                code="REOPTIMIZATION_INFEASIBLE",
                detail="No feasible re-optimized schedule exists given the current locks/configuration",
            ).model_dump(),
        )
    except ReoptimizationInvalidInputError as exc:
        return JSONResponse(
            status_code=422,
            content=ReoptimizationInvalidInputErrorResponse(
                code="REOPTIMIZATION_INVALID_INPUT",
                detail="Re-optimization input is invalid",
                errors=tuple(
                    validation_diagnostic_response_from_error(e) for e in exc.validation_errors
                ),
            ).model_dump(),
        )
    return active_schedule_response_from_active_version(active)
