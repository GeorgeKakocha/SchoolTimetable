from datetime import time

from school_timetable.domain.calendar import (
    Period,
    clock_time_overlaps,
    consecutive_period_pairs,
    derive_starts_new_block,
    recompute_block_ids,
)
from school_timetable.fixtures.common import build_days, build_periods


def test_build_days_and_periods_shape():
    days = build_days()
    periods = build_periods()
    assert len(days) == 5
    assert len(periods) == 8
    assert all(p.is_instructional for p in periods)


def test_consecutive_pairs_excludes_lunch_boundary():
    periods = build_periods()
    pairs = consecutive_period_pairs(periods)
    pair_ids = {(a.id, b.id) for a, b in pairs}

    # p4 (morning) and p5 (afternoon) straddle the lunch break and must
    # never be offered as a double-lesson pair.
    assert ("p4", "p5") not in pair_ids

    # Every other adjacent pair within a block should be present.
    assert ("p1", "p2") in pair_ids
    assert ("p3", "p4") in pair_ids
    assert ("p5", "p6") in pair_ids
    assert ("p7", "p8") in pair_ids

    # Exactly 3 pairs per block of 4 periods, 2 blocks -> 6 total.
    assert len(pairs) == 6


def test_consecutive_pairs_respects_explicit_block_ids():
    periods = (
        Period(id="a", name="A", index=0, block_id="x"),
        Period(id="b", name="B", index=1, block_id="y"),
        Period(id="c", name="C", index=2, block_id="y"),
    )
    pairs = consecutive_period_pairs(periods)
    assert [(p.id, q.id) for p, q in pairs] == [("b", "c")]


# -- Calendar A: clock_time_overlaps -----------------------------------------


def test_clock_time_overlaps_no_times_set_is_clean():
    periods = build_periods()
    assert clock_time_overlaps(periods) == []


def test_clock_time_overlaps_touching_boundary_allowed():
    periods = (
        Period(id="a", name="A", index=0, block_id="b0", start_time=time(9, 0), end_time=time(9, 45)),
        Period(id="b", name="B", index=1, block_id="b0", start_time=time(9, 45), end_time=time(10, 30)),
    )
    assert clock_time_overlaps(periods) == []


def test_clock_time_overlaps_gap_allowed():
    periods = (
        Period(id="a", name="A", index=0, block_id="b0", start_time=time(9, 0), end_time=time(9, 45)),
        Period(id="b", name="B", index=1, block_id="b1", start_time=time(10, 30), end_time=time(11, 15)),
    )
    assert clock_time_overlaps(periods) == []


def test_clock_time_overlaps_detects_reversed_overlap():
    periods = (
        Period(id="a", name="A", index=0, block_id="b0", start_time=time(9, 0), end_time=time(10, 0)),
        Period(id="b", name="B", index=1, block_id="b0", start_time=time(9, 30), end_time=time(10, 30)),
    )
    violations = clock_time_overlaps(periods)
    assert [(a.id, b.id) for a, b in violations] == [("a", "b")]


def test_clock_time_overlaps_skips_pair_with_one_missing_time():
    periods = (
        Period(id="a", name="A", index=0, block_id="b0", start_time=time(9, 0), end_time=time(10, 0)),
        Period(id="b", name="B", index=1, block_id="b0", start_time=None, end_time=None),
        Period(id="c", name="C", index=2, block_id="b0", start_time=time(8, 0), end_time=time(8, 30)),
    )
    # b has no times at all, so neither (a, b) nor (b, c) can be flagged --
    # only genuinely comparable neighboring pairs are ever inspected.
    assert clock_time_overlaps(periods) == []


def test_clock_time_overlaps_ignores_block_boundary_and_spans_whole_day():
    # Clock time overlap-checking is index-adjacency based, not
    # block_id-based -- a lunch boundary (different block_id) still
    # gets its clock times checked for order.
    periods = (
        Period(id="a", name="A", index=0, block_id="morning", start_time=time(12, 0), end_time=time(13, 0)),
        Period(id="b", name="B", index=1, block_id="afternoon", start_time=time(12, 30), end_time=time(14, 0)),
    )
    violations = clock_time_overlaps(periods)
    assert [(a.id, b.id) for a, b in violations] == [("a", "b")]


# -- Calendar A: derive_starts_new_block / recompute_block_ids ---------------


def test_derive_starts_new_block_first_period_always_starts_block():
    periods = build_periods()
    markers = derive_starts_new_block(periods)
    assert markers["p1"] is True


def test_derive_starts_new_block_matches_block_id_transitions():
    periods = build_periods()
    markers = derive_starts_new_block(periods)
    # p1..p4 share "morning"; p5..p8 share "afternoon" -- only p1 (first)
    # and p5 (block change) start a new block.
    assert markers == {
        "p1": True, "p2": False, "p3": False, "p4": False,
        "p5": True, "p6": False, "p7": False, "p8": False,
    }


def test_recompute_block_ids_first_id_always_starts_block_regardless_of_marker():
    ids = ["a", "b", "c"]
    markers = {"a": False, "b": False, "c": True}
    result = recompute_block_ids(ids, markers)
    assert result == {"a": "block_0", "b": "block_0", "c": "block_1"}


def test_recompute_block_ids_matches_derive_starts_new_block_round_trip():
    periods = build_periods()
    ordered_ids = [p.id for p in sorted(periods, key=lambda p: p.index)]
    markers = derive_starts_new_block(periods)
    block_ids = recompute_block_ids(ordered_ids, markers)
    assert block_ids["p1"] == block_ids["p2"] == block_ids["p3"] == block_ids["p4"]
    assert block_ids["p5"] == block_ids["p6"] == block_ids["p7"] == block_ids["p8"]
    assert block_ids["p1"] != block_ids["p5"]


def test_recompute_block_ids_preserves_marker_across_reorder():
    # Swap b and c; b's own "starts_new_block" marker (True) travels
    # with its row identity, landing at the new position.
    ids = ["a", "c", "b"]
    markers = {"a": True, "b": True, "c": False}
    result = recompute_block_ids(ids, markers)
    assert result == {"a": "block_0", "c": "block_0", "b": "block_1"}
