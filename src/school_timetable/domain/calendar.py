"""Calendar and time-structure concepts: academic year, days, periods, slots.

These types describe the *shape* of a school week. Nothing here is
hard-coded to a specific number of days or periods -- that shape is always
supplied by a concrete ``SchedulingProblem`` instance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AcademicYear:
    id: str
    label: str


@dataclass(frozen=True)
class Day:
    """One school day (e.g. Monday). ``index`` gives the day's position in
    the week, used only for ordering/soft-scoring, never for solver logic
    that assumes a fixed count of days.
    """

    id: str
    name: str
    index: int


@dataclass(frozen=True)
class Period:
    """One instructional period slot within a day.

    ``block_id`` groups periods that are contiguous in time with no
    structural break (such as lunch) between them. Two periods are only
    "consecutive" for double-lesson purposes when they share a
    ``block_id`` and their ``index`` values differ by exactly one -- this
    is how a lunch boundary (or any other break) is represented, without
    lunch itself needing to be modeled as a period.
    """

    id: str
    name: str
    index: int
    block_id: str
    is_instructional: bool = True


@dataclass(frozen=True)
class TimeSlot:
    """A concrete (day, period) coordinate."""

    day_id: str
    period_id: str

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        return f"TimeSlot({self.day_id}, {self.period_id})"


def consecutive_period_pairs(periods: tuple[Period, ...]) -> list[tuple[Period, Period]]:
    """Return every pair of periods that are adjacent within the same
    structural block, i.e. valid candidates for hosting a double lesson.

    A pair spanning a break (different ``block_id``) is never returned,
    which is how "do not cross the lunch boundary" is enforced structurally
    rather than by a magic period number.

    Kept as-is (rather than rewritten in terms of ``period_windows``) so
    the Phase-1 PREFERRED double-lesson encoding, which depends on it,
    is not put at risk of a subtle behavior change.
    """
    by_index = sorted(periods, key=lambda p: p.index)
    pairs: list[tuple[Period, Period]] = []
    for a, b in zip(by_index, by_index[1:]):
        if b.index == a.index + 1 and a.block_id == b.block_id:
            pairs.append((a, b))
    return pairs


def period_runs(periods: tuple[Period, ...]) -> list[tuple[Period, ...]]:
    """Split periods into maximal runs that are consecutive in ``index``
    and share a ``block_id`` -- the contiguous stretches with no
    structural break (e.g. lunch) inside them.
    """
    by_index = sorted(periods, key=lambda p: p.index)
    runs: list[list[Period]] = []
    for p in by_index:
        if runs and p.index == runs[-1][-1].index + 1 and p.block_id == runs[-1][-1].block_id:
            runs[-1].append(p)
        else:
            runs.append([p])
    return [tuple(run) for run in runs]


def period_windows(periods: tuple[Period, ...], length: int) -> list[tuple[Period, ...]]:
    """Every valid consecutive window of exactly ``length`` periods that
    stays entirely inside one structural run -- i.e. every placement a
    lesson block of that length could legally occupy without crossing a
    break boundary. Returns an empty list if no run is long enough to fit
    ``length`` (this is the generic, size-independent basis for both
    preflight's placeability check and the CP-SAT block encoding).
    """
    if length < 1:
        return []
    windows: list[tuple[Period, ...]] = []
    for run in period_runs(periods):
        for start in range(0, len(run) - length + 1):
            windows.append(run[start:start + length])
    return windows
