"""Acceptance tests for convo-coach. Claude and the Omi push are faked; nothing hits
the network. "#N" in a docstring is item N of the spec's acceptance list
(omi_backend repo: docs/superpowers/specs/2026-10-05-convo-coach-design.md)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import app as coach

UID = "uid-1"
GOOD = "What got you into rock climbing in the first place?"


def me(text):
    return {"text": text, "is_user": True}


def other(text):
    return {"text": text, "is_user": False}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    coach.sessions.clear()
    monkeypatch.setattr(coach, "SILENCE_S", 3600)  # silence tests shorten it themselves


def test_codeword_arms_only_from_the_user():
    """#4"""
    s = coach.Session()
    coach.ingest(s, [other("oh me opener")])
    assert not coach.opener_live(s)
    coach.ingest(s, [me("Oh, me opener.")])
    assert coach.opener_live(s)


def test_codeword_context_is_the_rest_of_the_users_speech():
    s = coach.Session()
    coach.ingest(s, [me("Omi opener: coffee shop, guy reading Dune")])
    assert s.opener_context == "coffee shop guy reading dune"
    s = coach.Session()
    coach.ingest(s, [me("omi opener")])
    coach.ingest(s, [other("next please"), me("At the gym, marathon shirt")])
    assert s.opener_context == "at the gym marathon shirt"


def test_codeword_needs_the_whole_phrase():
    s = coach.Session()
    coach.ingest(s, [me("naomi opener"), me("the opener was great")])
    assert not coach.opener_live(s)


def test_armed_opener_expires_after_30s():
    """#5"""
    s = coach.Session()
    coach.ingest(s, [me("omi opener")])
    s.opener_armed_at -= 29
    assert coach.opener_live(s)
    s.opener_armed_at -= 2
    assert not coach.opener_live(s)


def test_over_12_words_is_dropped_not_truncated():
    """#6"""
    twelve = "So what was the very best part of that whole trip, really?"
    assert len(twelve.split()) == 12
    assert coach.validate(twelve, []) == twelve
    assert coach.validate(twelve + " Honestly", []) is None


def test_newline_and_malformed_output_is_dropped():
    """#7"""
    assert coach.validate("Ask about the trip.\nOr the dog.", []) is None
    assert coach.validate("", []) is None
    assert coach.validate('""', []) is None
    assert coach.validate("NONE", []) is None
    assert coach.validate("Why?", []) is None  # Omi ignores messages of 5 chars or fewer
    assert coach.validate(GOOD, [GOOD]) is None  # repeat of a recent suggestion


def test_one_surrounding_quote_pair_is_stripped():
    assert coach.validate(' "What was the best part?" ', []) == "What was the best part?"
    assert coach.validate("“What was the best part?”", []) == "What was the best part?"
    inner = 'What did "fine" mean to you there?'
    assert coach.validate(inner, []) == inner


def test_buffer_keeps_only_the_last_120s():
    s = coach.Session()
    coach.ingest(s, [other("old news")])
    s.segments = [(t - 121, seq, seg) for t, seq, seg in s.segments]
    coach.ingest(s, [other("fresh news")])
    assert [seg["text"] for _, _, seg in s.segments] == ["fresh news"]
    assert s.seq == 2  # seq never shrinks, so last_checked stays valid across trims


def test_new_other_speech_ignores_the_user_and_checked_segments():
    s = coach.Session()
    coach.ingest(s, [me("I love that place")])
    assert not coach.new_other_speech(s)
    coach.ingest(s, [other("Me too, we go every summer")])
    assert coach.new_other_speech(s)
    s.last_checked = s.seq
    assert not coach.new_other_speech(s)


def test_transcript_labels_speakers_without_names():
    s = coach.Session()
    coach.ingest(s, [me("Hi"), {"text": "Hey", "is_user": False, "speaker": "SPEAKER_01"}])
    assert coach.transcript(s) == "USER: Hi\nOTHER: Hey"
