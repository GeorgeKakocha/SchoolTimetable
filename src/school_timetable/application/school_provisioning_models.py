"""Application-owned contract for provisioning a School and its first year.

These plain dataclasses contain public/natural identifiers only.  They are
neither HTTP schemas nor persistence rows.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.application.configuration_revision_models import ConfigurationRevisionState


@dataclass(frozen=True)
class ProvisionSchoolWithInitialYearCommand:
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str


@dataclass(frozen=True)
class ProvisionedSchool:
    id: str
    name: str


@dataclass(frozen=True)
class ProvisionedAcademicYear:
    id: str
    label: str


@dataclass(frozen=True)
class ProvisionSchoolWithInitialYearResult:
    school: ProvisionedSchool
    academic_year: ProvisionedAcademicYear
    configuration_state: ConfigurationRevisionState
