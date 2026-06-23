"""Pure overlap/closest helpers for queries Firestore can't express directly.

Firestore forbids inequality filters on two different fields in one query. The DB
layer applies ONE inequality server-side; these functions apply the second bound
(and any closest-pick) in Python. No Firestore imports — unit-testable in isolation.
"""


def filter_overlapping_meetings(meetings: list, range_start, range_end) -> list:
    """Keep meetings overlapping [range_start, range_end): start_time < range_end AND
    end_time > range_start. Sorted by start_time ascending. Skips items missing either field."""
    out = [
        m for m in meetings
        if m.get('start_time') is not None
        and m.get('end_time') is not None
        and m['start_time'] < range_end
        and m['end_time'] > range_start
    ]
    out.sort(key=lambda m: m['start_time'])
    return out


def closest_conversation_by_timestamps(conversations, start_threshold, end_threshold, start_timestamp, end_timestamp):
    """From candidates (already filtered server-side by started_at <= end_threshold), keep
    those with finished_at >= start_threshold, then return the one whose start or end is
    closest to the target timestamps. Returns None if none qualify."""
    closest = None
    min_diff = float('inf')
    for c in conversations:
        finished_at = c.get('finished_at')
        started_at = c.get('started_at')
        if finished_at is None or started_at is None or finished_at < start_threshold:
            continue
        diff = min(abs(started_at.timestamp() - start_timestamp), abs(finished_at.timestamp() - end_timestamp))
        if diff < min_diff:
            min_diff = diff
            closest = c
    return closest
