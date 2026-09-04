"""Centralized soft-constraint weights.

Every soft penalty term in the objective function is scaled through this
module. No other file in ``scheduling`` should contain a bare penalty
integer -- if a new soft constraint is added, its weight tier belongs here.
"""
from __future__ import annotations

from school_timetable.domain.requirements import PreferenceWeight

PREFERENCE_WEIGHT_VALUES: dict[PreferenceWeight, int] = {
    PreferenceWeight.LOW: 1,
    PreferenceWeight.MEDIUM: 5,
    PreferenceWeight.HIGH: 20,
}

# Fixed tiers for soft constraint categories that are not driven by a
# school-supplied PreferenceWeight on the domain object.
TEACHER_PREFER_NOT_WEIGHT_TIER = PreferenceWeight.MEDIUM
PREFERRED_DOUBLE_WEIGHT_TIER = PreferenceWeight.HIGH
MIN_DISTINCT_DAYS_WEIGHT_TIER = PreferenceWeight.LOW


def weight_value(tier: PreferenceWeight) -> int:
    return PREFERENCE_WEIGHT_VALUES[tier]
