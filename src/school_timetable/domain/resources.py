"""Shared physical/scheduling resources (e.g. a gym) and their use."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    capacity: int = 1


@dataclass(frozen=True)
class ResourceRequirement:
    """States that a TeachingRequirement needs a given resource for every
    one of its scheduled lessons."""

    resource_id: str
