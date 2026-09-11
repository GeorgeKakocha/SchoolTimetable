"""Calendar and time-structure concepts: academic year, days, periods, slots.

These types describe the *shape* of a school week. Nothing here is
hard-coded to a specific number of days or periods -- that shape is always
supplied by a concrete ``SchedulingProblem`` instance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


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
    start_time: time | None = None
    """Calendar A: optional bell-clock metadata, display-only -- the
    solver/preflight/verifier never read this field. Either both
    `start_time` and `end_time` are set, or neither is (enforced by
    `calendar_rules`, not by this dataclass itself)."""
    end_time: time | None = None


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


def clock_time_overlaps(periods: tuple[Period, ...]) -> list[tuple[Period, Period]]:
    """Calendar A: every pair of Periods that are adjacent *by index*
    (regardless of ``block_id`` -- clock time spans the whole day, not
    just one structural block) whose bell times are out of order or
    overlapping. For consecutive ``A`` then ``B``, ``A.end_time <=
    B.start_time`` is required whenever *both* carry clock times. A
    Period missing either ``start_time``/``end_time`` is never used to
    infer ordering against its neighbor -- that pair is simply skipped,
    never flagged. Pure and reused by both `validation.preflight` (the
    authoritative structural check) and `application.calendar_rules`
    (the write-time fast precheck), so the two can never diverge.
    """
    by_index = sorted(periods, key=lambda p: p.index)
    violations: list[tuple[Period, Period]] = []
    for a, b in zip(by_index, by_index[1:]):
        if a.start_time is None or a.end_time is None or b.start_time is None or b.end_time is None:
            continue
        if a.end_time > b.start_time:
            violations.append((a, b))
    return violations


def derive_starts_new_block(periods_sorted_by_index: tuple[Period, ...]) -> dict[str, bool]:
    """Calendar A: the read-side derivation of the public
    ``starts_new_block`` field (never a raw ``block_id``) -- the first
    Period always starts a new block; a later one does iff its
    ``block_id`` differs from the immediately preceding Period's. Also
    reused write-side (`calendar_service`/`calendar_repository`) to
    recover each *other*, untouched Period's own current marker before
    recomputing the whole sequence's block assignment."""
    markers: dict[str, bool] = {}
    previous_block_id: str | None = None
    for period in periods_sorted_by_index:
        markers[period.id] = previous_block_id is None or period.block_id != previous_block_id
        previous_block_id = period.block_id
    return markers


def recompute_block_ids(ordered_period_ids: list[str], marker_by_id: dict[str, bool]) -> dict[str, str]:
    """Calendar A: the single, deterministic function that recomputes
    every Period's internal ``block_id`` from an ordered list of Period
    ids plus each one's own ``starts_new_block`` marker (see
    ``derive_starts_new_block``) -- shared by create/update/delete/move
    so block segmentation is recomputed identically after every kind of
    mutation. The first id in the list always starts ``block_0``
    regardless of its own marker value (the first Period always starts
    a new block); canonical labels are internal-only
    (``block_0``, ``block_1``, ...), never exposed on any public API.
    """
    block_ids: dict[str, str] = {}
    current_block_number = -1
    for position, period_id in enumerate(ordered_period_ids):
        starts_new_block = True if position == 0 else marker_by_id.get(period_id, False)
        if starts_new_block:
            current_block_number += 1
        block_ids[period_id] = f"block_{current_block_number}"
    return block_ids
