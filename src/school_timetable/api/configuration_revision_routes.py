"""`GET /schools/{school_id}/years/{year_id}/configuration/state`,
`POST .../configuration/draft`, and `DELETE .../configuration/draft`
(Safe Configuration Changes, Slice B): the draft configuration
lifecycle -- read revision state, open (or idempotently reuse) an
editable draft eagerly cloned from the current published revision, and
discard an open draft.

All three routes depend only on `application.ports.
ConfigurationRevisionRepository` directly -- the same way `api/
config_routes.py`'s existing `GET /config` route depends on
`SchedulingProblemRepository` directly -- never on a concrete
`persistence/` class; none of the three has any additional
application-level orchestration beyond what the concrete adapter
already does inside its own `AcademicYear`-row-locked transaction, so
no separate `application/` service class wraps this port.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching `/config`'s existing convention exactly.

Error mapping:
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing `/config` body.
- (`DELETE` only) `NoConfigurationDraftError` -> 409, `{"code":
  "NO_CONFIGURATION_DRAFT", "detail": "..."}`.
- (`DELETE` only) `InitialDraftCannotBeDiscardedError` -> 409, `{"code":
  "INITIAL_DRAFT_CANNOT_BE_DISCARDED", "detail": "..."}`.

Deliberately NOT caught here: an internal-invariant `RuntimeError`
(a corrupt `AcademicYear` with neither a draft nor a published
revision, or -- unreachable under Slice A's own invariant -- a draft
somehow still referenced by a `ScheduleVersion`) is left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, never
disguised as a client-actionable outcome.

No frontend feature work accompanies this slice -- these routes exist
so a later UI slice has something to call; no draft banner, no Discard
button, and no Regenerate button exist yet.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_configuration_revision_repository
from school_timetable.api.schemas import (
    ConfigurationRevisionStateResponse,
    InitialDraftCannotBeDiscardedErrorResponse,
    NoConfigurationDraftErrorResponse,
)
from school_timetable.application.errors import (
    InitialDraftCannotBeDiscardedError,
    NoConfigurationDraftError,
    SchedulingProblemNotFoundError,
)
from school_timetable.application.ports import ConfigurationRevisionRepository

router = APIRouter()


def _state_response(state) -> ConfigurationRevisionStateResponse:
    return ConfigurationRevisionStateResponse(
        published_revision_number=state.published_revision_number,
        draft_revision_number=state.draft_revision_number,
        configuration_locked=state.configuration_locked,
        timetable_out_of_date=state.timetable_out_of_date,
    )


@router.get(
    "/schools/{school_id}/years/{year_id}/configuration/state",
    response_model=ConfigurationRevisionStateResponse,
)
def get_configuration_revision_state(
    school_id: str,
    year_id: str,
    repository: ConfigurationRevisionRepository = Depends(get_configuration_revision_repository),
) -> ConfigurationRevisionStateResponse:
    try:
        state = repository.get_state(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return _state_response(state)


@router.post(
    "/schools/{school_id}/years/{year_id}/configuration/draft",
    response_model=ConfigurationRevisionStateResponse,
)
def begin_configuration_draft(
    school_id: str,
    year_id: str,
    repository: ConfigurationRevisionRepository = Depends(get_configuration_revision_repository),
) -> ConfigurationRevisionStateResponse:
    try:
        state = repository.begin_draft(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return _state_response(state)


@router.delete(
    "/schools/{school_id}/years/{year_id}/configuration/draft",
    response_model=ConfigurationRevisionStateResponse,
)
def discard_configuration_draft(
    school_id: str,
    year_id: str,
    repository: ConfigurationRevisionRepository = Depends(get_configuration_revision_repository),
):
    try:
        state = repository.discard_draft(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except NoConfigurationDraftError as exc:
        return JSONResponse(
            status_code=409,
            content=NoConfigurationDraftErrorResponse(
                code="NO_CONFIGURATION_DRAFT", detail=str(exc),
            ).model_dump(),
        )
    except InitialDraftCannotBeDiscardedError as exc:
        return JSONResponse(
            status_code=409,
            content=InitialDraftCannotBeDiscardedErrorResponse(
                code="INITIAL_DRAFT_CANNOT_BE_DISCARDED", detail=str(exc),
            ).model_dump(),
        )
    return _state_response(state)
