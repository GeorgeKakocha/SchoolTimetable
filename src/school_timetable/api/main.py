"""FastAPI application shell (Phase 3A1+).

`GET /health` is a real, database-backed health check (it actually
executes a query; it never fakes success). Phase 3A2.4 adds the first
domain/business endpoint, `GET /schools/{school_id}/years/{year_id}/config`
(read-only scheduling configuration) -- see `api/config_routes.py`.
Phase 3A3.4 adds `GET .../schedule/active` and
`POST .../schedule/generate` -- see `api/schedule_routes.py`.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from school_timetable.api.config_routes import router as config_router
from school_timetable.api.schedule_routes import router as schedule_router
from school_timetable.persistence.db import get_session

app = FastAPI(title="School Timetable API")
app.include_router(config_router)
app.include_router(schedule_router)


class HealthResponse(BaseModel):
    status: str
    database: str


@app.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_session)) -> JSONResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content=HealthResponse(status="error", database="unreachable").model_dump(),
        )
    return JSONResponse(status_code=200, content=HealthResponse(status="ok", database="ok").model_dump())
