"""Teaching requirements: the core scheduling input.

A ``TeachingRequirement`` says: this teacher must deliver this
subject/activity to this participant group, N times a week, subject to a
block/distribution shape and time preferences. The solver never chooses
*who* teaches *what* -- that is already decided by the school and encoded
here. The solver only decides *when* each required lesson occurs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from school_timetable.domain.resources import ResourceRequirement


class PreferenceWeight(str, Enum):
    """Coarse, school-facing priority tiers for soft preferences.

    The mapping from these tiers to concrete solver penalty integers is
    centralized in ``scheduling.weights`` and never scattered as magic
    numbers through solver code.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BlockPolicyMode(str, Enum):
    REQUIRED = "REQUIRED"
    """The block pattern must be respected exactly; violating it is infeasible."""

    PREFERRED = "PREFERRED"
    """The solver may break the pattern, at a soft-penalty cost."""

    FLEXIBLE = "FLEXIBLE"
    """The solver freely places the weekly periods with no pattern shape."""


@dataclass(frozen=True)
class LessonBlockPolicy:
    """Describes how a requirement's weekly periods should be grouped into
    lesson blocks (e.g. one double lesson plus three singles).

    ``block_sizes`` is a multiset of block lengths that must sum to the
    requirement's ``weekly_periods`` whenever ``mode`` is REQUIRED or
    PREFERRED (ignored, and expected empty, for FLEXIBLE).

    This PoC's solver supports at most a single block of size 2 (a double
    lesson) per requirement, with the remainder as size-1 singles -- e.g.
    ``(2, 1, 1, 1)``. This covers every block shape needed by the fixtures
    while keeping the CP-SAT encoding simple; it is a solver-implementation
    limitation, not a hard-coded subject rule, and is enforced explicitly
    by preflight validation rather than silently accepted or ignored.
    """

    mode: BlockPolicyMode
    block_sizes: tuple[int, ...] = ()

    @property
    def has_double(self) -> bool:
        return self.block_sizes.count(2) > 0


@dataclass(frozen=True)
class DistributionPolicy:
    """Constraints on how a requirement's lessons spread across the week.

    ``max_periods_per_day`` is HARD when set.
    ``min_distinct_days`` is a SOFT preference when set.
    """

    min_distinct_days: int | None = None
    max_periods_per_day: int | None = None


@dataclass(frozen=True)
class TimePreference:
    """A soft preference for which periods (any day) a requirement's
    lessons should land on."""

    preferred_periods: tuple[int, ...]
    """Period ``index`` values (see ``Period.index``) that are preferred."""

    weight: PreferenceWeight = PreferenceWeight.MEDIUM


@dataclass(frozen=True)
class TeachingRequirement:
    id: str
    teacher_id: str
    activity_id: str
    participant_group_id: str
    weekly_periods: int
    block_policy: LessonBlockPolicy = field(
        default_factory=lambda: LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)
    )
    distribution_policy: DistributionPolicy = field(default_factory=DistributionPolicy)
    time_preferences: tuple[TimePreference, ...] = ()
    resource_requirement: ResourceRequirement | None = None
    split_group_id: str | None = None
    """Requirements sharing the same split_group_id are parallel branches of
    the same split block (e.g. German/Russian) and must always be scheduled
    in lock-step, in the same slots, with their own teachers."""
