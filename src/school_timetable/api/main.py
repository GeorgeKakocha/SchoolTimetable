"""FastAPI application shell (Phase 3A1).

No domain/business endpoints yet -- just the app instance and a real,
database-backed health check (`GET /health` actually executes a query; it
never fakes success). Application services and repository ports are
introduced starting Phase 3A2.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from school_timetable.persistence.db import get_session

app = FastAPI(title="School Timetable API")


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
