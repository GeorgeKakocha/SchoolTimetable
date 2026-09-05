"""Typed domain model for the school timetable solver.

Pure Python, no OR-Tools dependency, no I/O. This is the vocabulary that
preflight validation, the solver, and the independent verifier all share.
"""
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import (
    AcademicYear,
    Day,
    Period,
    TimeSlot,
    consecutive_period_pairs,
    period_runs,
    period_windows,
)
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    PreferenceWeight,
    TeachingRequirement,
    TimePreference,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry, SchedulingResult, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.domain.school import School

__all__ = [
    "Activity",
    "ActivityKind",
    "FixedPlacement",
    "ReservedBlock",
    "AcademicYear",
    "Day",
    "Period",
    "TimeSlot",
    "consecutive_period_pairs",
    "period_runs",
    "period_windows",
    "ClassSection",
    "ParticipantGroup",
    "AvailabilityStatus",
    "Teacher",
    "TeacherAvailability",
    "SchedulingProblem",
    "BlockPolicyMode",
    "DistributionPolicy",
    "LessonBlockPolicy",
    "PreferenceWeight",
    "TeachingRequirement",
    "TimePreference",
    "Resource",
    "ResourceRequirement",
    "EntrySource",
    "ScheduleEntry",
    "SchedulingResult",
    "SolverStatus",
    "OccurrenceKey",
    "Schedule",
    "School",
]
