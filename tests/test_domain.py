from school_timetable.domain.calendar import Period, consecutive_period_pairs
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
