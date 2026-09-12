"""Read model for Safe Configuration Changes, Slice B: the draft
configuration lifecycle. Exposes exactly enough state for a caller (a
future UI slice, or this slice's own backend guards) to know whether
scheduling configuration is currently editable and whether the active
timetable is out of date -- never a persistence surrogate ID, never the
`ConfigurationRevision` model itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigurationRevisionState:
    """One `AcademicYear`'s current configuration-revision state.

    `published_revision_number`/`draft_revision_number` are natural
    `ConfigurationRevision.revision_number` values (never a persistence
    surrogate `id`) -- `None` exactly when no revision of that status
    exists for this year (a brand-new year has no published revision
    yet; a year with no open draft has `draft_revision_number = None`).

    `configuration_locked` and `timetable_out_of_date` are DERIVED, not
    persisted anywhere -- `persistence/configuration_revision_repository.py`
    computes both, every time, from `draft_revision_number`/`has_schedule`
    alone:

    - `configuration_locked` is `True` exactly when no draft exists
      (`draft_revision_number is None`) -- before Slice B this was
      equivalent to "a Schedule exists"; Slice B's "Begin editing
      configuration" reopens a draft for a year that already has a
      Schedule, so the two conditions are no longer the same thing.
    - `timetable_out_of_date` is `True` exactly when a Schedule exists
      AND a draft is currently open -- Owner Decision 1 (Slice B): the
      active timetable remains viewable but is considered stale and
      read-only while its configuration is being edited.
    """

    published_revision_number: int | None
    draft_revision_number: int | None
    has_schedule: bool
    configuration_locked: bool
    timetable_out_of_date: bool
