"""FastAPI application shell (Phase 3A1+).

`GET /health` is a real, database-backed health check (it actually
executes a query; it never fakes success). Phase 3A2.4 adds the first
domain/business endpoint, `GET /schools/{school_id}/years/{year_id}/config`
(read-only scheduling configuration) -- see `api/config_routes.py`.
Phase 3A3.4 adds `GET .../schedule/active` and
`POST .../schedule/generate` -- see `api/schedule_routes.py`. Phase
3C.2b adds the Teaching Assignments read projection and write routes --
see `api/teaching_assignment_routes.py`. Real-School Setup MVP Slice B
adds the Teacher CRUD read projection and write routes -- see
`api/teacher_routes.py`. Slice C adds the Class CRUD read projection
and write routes -- see `api/class_section_routes.py`. Slice D adds
the Subject CRUD read projection and write routes -- see
`api/subject_routes.py`. Owner Decision #38 adds the Teacher
Availability read projection and teacher-scoped bulk write route --
see `api/teacher_availability_routes.py`. Reserved Activities Slice A1
adds the Special Activity CRUD read projection and write routes --
see `api/special_activity_routes.py`. Reserved Activities Slice A2
adds the Reserved Activity CRUD read projection and write routes --
see `api/reserved_activity_routes.py`. Resources Slice A adds the
Resource catalog CRUD read projection and write routes -- see
`api/resource_routes.py`.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from school_timetable.api.calendar_routes import router as calendar_router
from school_timetable.api.class_section_routes import router as class_section_router
from school_timetable.api.config_routes import router as config_router
from school_timetable.api.configuration_revision_routes import router as configuration_revision_router
from school_timetable.api.reserved_activity_routes import router as reserved_activity_router
from school_timetable.api.resource_routes import router as resource_router
from school_timetable.api.schedule_routes import router as schedule_router
from school_timetable.api.school_provisioning_routes import router as school_provisioning_router
from school_timetable.api.special_activity_routes import router as special_activity_router
from school_timetable.api.subject_routes import router as subject_router
from school_timetable.api.teacher_availability_routes import router as teacher_availability_router
from school_timetable.api.teacher_routes import router as teacher_router
from school_timetable.api.teaching_assignment_routes import router as teaching_assignment_router
from school_timetable.persistence.db import get_session

app = FastAPI(title="School Timetable API")
app.include_router(school_provisioning_router)
app.include_router(config_router)
app.include_router(configuration_revision_router)
app.include_router(schedule_router)
app.include_router(teaching_assignment_router)
app.include_router(teacher_router)
app.include_router(class_section_router)
app.include_router(subject_router)
app.include_router(teacher_availability_router)
app.include_router(special_activity_router)
app.include_router(reserved_activity_router)
app.include_router(resource_router)
app.include_router(calendar_router)


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
