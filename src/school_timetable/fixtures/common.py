"""Shared calendar-shape builder used by the synthetic fixtures.

5 school days, 8 instructional periods per day, with a structural break
(lunch) between period 4 and period 5 -- periods 1-4 share block "morning"
and periods 5-8 share block "afternoon", so no double lesson can ever be
built across that boundary.
"""
from __future__ import annotations

from school_timetable.domain.calendar import Day, Period

DAY_DEFS = [
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
]


def build_days() -> tuple[Day, ...]:
    return tuple(Day(id=day_id, name=name, index=i) for i, (day_id, name) in enumerate(DAY_DEFS))


def build_periods() -> tuple[Period, ...]:
    periods = []
    for i in range(1, 9):
        block = "morning" if i <= 4 else "afternoon"
        periods.append(Period(id=f"p{i}", name=f"Period {i}", index=i - 1, block_id=block))
    return tuple(periods)
