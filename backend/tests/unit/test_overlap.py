"""Unit tests for the pure overlap/closest helpers (no Firestore)."""

from datetime import datetime, timezone

from utils.conversations.overlap import (
    closest_conversation_by_timestamps,
    filter_overlapping_meetings,
)


def _dt(h, m=0):
    return datetime(2026, 6, 20, h, m, tzinfo=timezone.utc)


def test_filter_overlapping_meetings_keeps_only_overlaps_sorted():
    rng_start, rng_end = _dt(10), _dt(11)
    meetings = [
        {'id': 'before', 'start_time': _dt(8), 'end_time': _dt(9)},  # ends before range -> out
        {'id': 'overlap_late', 'start_time': _dt(10, 30), 'end_time': _dt(12)},  # in
        {'id': 'overlap_early', 'start_time': _dt(9), 'end_time': _dt(10, 30)},  # in
        {
            'id': 'after',
            'start_time': _dt(11),
            'end_time': _dt(12),
        },  # starts at range end -> out (start < end is false)
    ]
    out = filter_overlapping_meetings(meetings, rng_start, rng_end)
    assert [m['id'] for m in out] == ['overlap_early', 'overlap_late']  # sorted by start_time


def test_filter_overlapping_meetings_handles_missing_fields():
    out = filter_overlapping_meetings([{'id': 'x'}], _dt(10), _dt(11))
    assert out == []


def test_closest_conversation_picks_nearest_and_filters_lower_bound():
    start_ts, end_ts = _dt(10).timestamp(), _dt(11).timestamp()
    start_threshold, end_threshold = _dt(9, 58), _dt(11, 2)
    convs = [
        {'id': 'too_old', 'started_at': _dt(7), 'finished_at': _dt(8)},  # finished before threshold -> filtered
        {'id': 'near', 'started_at': _dt(10, 1), 'finished_at': _dt(10, 50)},  # closest
        {'id': 'far', 'started_at': _dt(9, 30), 'finished_at': _dt(10, 5)},
    ]
    got = closest_conversation_by_timestamps(convs, start_threshold, end_threshold, start_ts, end_ts)
    assert got['id'] == 'near'


def test_closest_conversation_returns_none_when_no_candidates():
    start_ts, end_ts = _dt(10).timestamp(), _dt(11).timestamp()
    convs = [{'id': 'old', 'started_at': _dt(7), 'finished_at': _dt(8)}]
    got = closest_conversation_by_timestamps(convs, _dt(9, 58), _dt(11, 2), start_ts, end_ts)
    assert got is None
