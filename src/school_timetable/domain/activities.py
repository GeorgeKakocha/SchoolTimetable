"""Subjects and other schedulable activities (including clubs)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActivityKind(str, Enum):
    ORDINARY = "ORDINARY"
    """A regular teaching subject, scheduled via a TeachingRequirement."""

    CLUB = "CLUB"
    """A reserved/club activity, scheduled via a ReservedBlock."""


@dataclass(frozen=True)
class Activity:
    id: str
    name: str
    kind: ActivityKind = ActivityKind.ORDINARY
