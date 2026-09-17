"""Shared projection of a teacher-visible schedule entry.

Both the one-teacher timetable and the whole-school Teacher Matrix expose
the same public/domain entry semantics.  Keeping the strict natural-ID to
display-value resolution here prevents those two read models from drifting
without coupling either service to the other's private implementation.
"""
from __future__ import annotations

from school_timetable.application.teacher_timetable_models import (
    TeacherTimetableClassSection,
    TeacherTimetableEntry,
)
from school_timetable.domain.groups import ParticipantGroup
from school_timetable.domain.result import ScheduleEntry


def project_teacher_timetable_entry(
    entry: ScheduleEntry,
    activities_by_id: dict[str, str],
    groups_by_id: dict[str, ParticipantGroup],
    class_sections_by_id: dict[str, str],
) -> TeacherTimetableEntry:
    """Resolve one entry using only its version's configuration graph.

    Referenced natural IDs are deliberately strict lookups.  A missing
    activity, participant group, or class section is a genuine inconsistent
    schedule/configuration pair and must surface rather than becoming an
    invented or blank label.
    """
    group = None if entry.participant_group_id is None else groups_by_id[entry.participant_group_id]
    resolved_class_sections = tuple(
        TeacherTimetableClassSection(id=class_id, name=class_sections_by_id[class_id])
        for class_id in entry.class_sections
    )
    return TeacherTimetableEntry(
        source=entry.source,
        activity_id=entry.activity_id,
        activity_name=activities_by_id[entry.activity_id],
        participant_group_id=entry.participant_group_id,
        participant_group_name=None if group is None else group.name,
        participant_group_role=None if group is None else group.role.value,
        class_sections=resolved_class_sections,
        requirement_id=entry.requirement_id,
        reserved_block_id=entry.reserved_block_id,
        resource_id=entry.resource_id,
    )
