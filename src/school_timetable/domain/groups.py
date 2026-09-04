"""Classes and participant groups.

``ClassSection`` and ``ParticipantGroup`` are deliberately distinct concepts:

- A ``ClassSection`` is an administrative class such as "8-A". Class
  occupancy (every main class fully scheduled) is checked per
  ``ClassSection``.
- A ``ParticipantGroup`` is *who actually attends a given lesson*. It may
  equal a whole class ("all of 8-A"), a subset that only exists because of
  a split ("8-A German"), or a union of classes ("11-A + 12-A merged").

A ``ParticipantGroup`` records which ``ClassSection``s it draws
students from via ``class_sections`` -- this is what lets the solver know
which classes are "occupied" whenever a lesson for that group is
scheduled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassSection:
    id: str
    name: str


@dataclass(frozen=True)
class ParticipantGroup:
    id: str
    name: str
    class_sections: tuple[str, ...]
    """IDs of the ClassSection(s) this group draws from / occupies.

    Exactly one class section for an ordinary or split-branch group;
    two or more for a merged group.
    """
