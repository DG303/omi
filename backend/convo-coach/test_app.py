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


def body(*segments):
    return {"session_id": UID, "segments": list(segments)}


def claude_says(monkeypatch, *outputs, gate=None, gate_on=1):
    """Fake Claude returning outputs in call order. With a gate, call number gate_on
    blocks until gate.set(); pass a gate to hold a call in flight."""
    calls = []
    queue = list(outputs)

    async def fake(mode, user_input, recent):
        calls.append((mode, user_input))
        out = queue.pop(0)
        if gate is not None and len(calls) == gate_on:
            await gate.wait()
        return out

    monkeypatch.setattr(coach, "ask_claude", fake)
    return calls


async def until(condition):
    for _ in range(1000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def in_flight():
    return UID in coach.sessions and coach.sessions[UID].llm_in_flight


def test_none_leaves_cooldown_untouched(monkeypatch):
    """#1"""
    calls = claude_says(monkeypatch, "NONE")
    assert asyncio.run(coach.handle(body(other("I just got back from Lisbon")))) == {}
    s = coach.sessions[UID]
    assert len(calls) == 1
    assert s.cooldown_until == 0.0
    assert s.last_suggestions == []


def test_valid_webhook_message_starts_cooldown(monkeypatch):
    """#2 (webhook half)"""
    calls = claude_says(monkeypatch, GOOD)

    async def run():
        assert await coach.handle(body(other("I've been climbing a lot lately"))) == {"message": GOOD}
        assert await coach.handle(body(other("Mostly bouldering, actually"))) == {}

    asyncio.run(run())
    s = coach.sessions[UID]
    assert len(calls) == 1  # the second webhook hit the cooldown
    assert s.cooldown_until > time.monotonic() + 40
    assert s.last_suggestions == [GOOD]


def test_user_only_speech_never_asks_for_a_followup(monkeypatch):
    calls = claude_says(monkeypatch)
    assert asyncio.run(coach.handle(body(me("So anyway, that was my weekend")))) == {}
    assert calls == []


def test_codeword_with_context_fires_an_opener_and_disarms(monkeypatch):
    opener = "That travel guide looks well used. Where are you headed?"
    calls = claude_says(monkeypatch, opener)
    result = asyncio.run(coach.handle(body(me("omi opener bookstore, woman holding a travel guide"))))
    assert result == {"message": opener}
    assert calls == [("opener", "Context: bookstore woman holding a travel guide")]
    assert not coach.opener_live(coach.sessions[UID])


def test_internal_exception_returns_200_empty(monkeypatch):
    """#8"""

    def boom(s, segments):
        raise RuntimeError("boom")

    monkeypatch.setattr(coach, "ingest", boom)
    client = TestClient(coach.app)
    r = client.post("/webhook", json=body(other("hello there")))
    assert r.status_code == 200
    assert r.json() == {}
    r = client.post("/webhook", content=b"not json")
    assert r.status_code == 200
    assert r.json() == {}


def test_reenable_probe_gets_200():
    r = TestClient(coach.app).post("/webhook", json={})
    assert r.status_code == 200
    assert r.json() == {}


def test_in_flight_call_blocks_a_second_call(monkeypatch):
    """#11"""
    gate = asyncio.Event()
    calls = claude_says(monkeypatch, GOOD, gate=gate)

    async def run():
        a = asyncio.create_task(coach.handle(body(other("I just started learning the cello"))))
        await until(in_flight)
        assert await coach.handle(body(other("It's harder than I expected"))) == {}
        assert len(calls) == 1
        assert coach.sessions[UID].segments[-1][2]["text"] == "It's harder than I expected"
        gate.set()
        assert await a == {"message": GOOD}
        assert len(calls) == 1  # A fired, so no re-evaluation

    asyncio.run(run())


def test_later_speech_is_checked_after_the_call_finishes(monkeypatch):
    """#12"""
    gate = asyncio.Event()
    calls = claude_says(monkeypatch, "NONE", "NONE", gate=gate)

    async def run():
        a = asyncio.create_task(coach.handle(body(other("We drove up the coast last weekend"))))
        await until(in_flight)
        gate.set()
        assert await a == {}
        assert not coach.sessions[UID].llm_in_flight
        assert await coach.handle(body(other("Stopped in Big Sur for a night"))) == {}

    asyncio.run(run())
    assert [mode for mode, _ in calls] == ["followup", "followup"]


def test_speech_during_a_none_call_is_reevaluated_without_another_webhook(monkeypatch):
    """#14"""
    gate = asyncio.Event()
    calls = claude_says(monkeypatch, "NONE", GOOD, gate=gate)

    async def run():
        a = asyncio.create_task(coach.handle(body(other("Work has been a lot lately"))))
        await until(in_flight)
        assert await coach.handle(body(other("We're moving to Denver in March"))) == {}
        gate.set()
        assert await a == {"message": GOOD}  # A's own request re-evaluated and fired

    asyncio.run(run())
    assert [mode for mode, _ in calls] == ["followup", "followup"]
    assert "Denver" in calls[1][1]


def push_returns(monkeypatch, ok):
    sent = []

    async def fake(uid, text):
        sent.append(text)
        return ok

    monkeypatch.setattr(coach, "push", fake)
    return sent


def test_successful_silence_push_starts_cooldown(monkeypatch):
    """#2 (silence half)"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.01)
    calls = claude_says(monkeypatch, "NONE", GOOD)
    sent = push_returns(monkeypatch, True)

    async def run():
        await coach.handle(body(other("We hiked Mount Tam on Sunday")))
        await asyncio.sleep(0.1)

    asyncio.run(run())
    s = coach.sessions[UID]
    assert [mode for mode, _ in calls] == ["followup", "restart"]
    assert sent == [GOOD]
    assert s.cooldown_until > time.monotonic() + 40
    assert s.last_suggestions == [GOOD]


def test_failed_silence_push_does_not_start_cooldown(monkeypatch):
    """#3"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.01)
    claude_says(monkeypatch, "NONE", GOOD)
    sent = push_returns(monkeypatch, False)

    async def run():
        await coach.handle(body(other("We hiked Mount Tam on Sunday")))
        await asyncio.sleep(0.1)

    asyncio.run(run())
    s = coach.sessions[UID]
    assert sent == [GOOD]
    assert s.cooldown_until == 0.0
    assert s.last_suggestions == []


def test_silence_fires_once_per_lull(monkeypatch):
    """#9"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.01)
    calls = claude_says(monkeypatch, "NONE", "NONE")
    push_returns(monkeypatch, True)

    async def run():
        await coach.handle(body(other("The new place on Fifth is great")))
        await asyncio.sleep(0.2)  # 20 silence periods; still only one fire

    asyncio.run(run())
    assert [mode for mode, _ in calls] == ["followup", "restart"]


def test_new_speech_cancels_a_pending_silence_timer(monkeypatch):
    """#10 (before it fires)"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.2)
    calls = claude_says(monkeypatch, "NONE", "NONE", "NONE")
    push_returns(monkeypatch, True)

    async def run():
        await coach.handle(body(other("I've been getting into pottery")))
        await asyncio.sleep(0.1)
        await coach.handle(body(other("Mostly mugs so far")))
        await asyncio.sleep(0.15)  # 0.25s after the first webhook: its timer would have fired
        assert [mode for mode, _ in calls] == ["followup", "followup"]
        await asyncio.sleep(0.15)
        assert [mode for mode, _ in calls] == ["followup", "followup", "restart"]

    asyncio.run(run())


def test_new_speech_cancels_a_silence_timer_mid_claude_call(monkeypatch):
    """#10 (mid-call)"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.01)
    gate = asyncio.Event()  # never set: the restart call hangs until cancelled
    calls = claude_says(monkeypatch, "NONE", GOOD, "NONE", gate=gate, gate_on=2)
    sent = push_returns(monkeypatch, True)

    async def run():
        await coach.handle(body(other("My sister just had twins")))
        await until(in_flight)
        timer = coach.sessions[UID].silence_task
        assert await coach.handle(body(other("So I'm an aunt twice over"))) == {}
        assert timer.cancelled()
        assert not coach.sessions[UID].llm_in_flight
        assert [mode for mode, _ in calls] == ["followup", "restart", "followup"]
        assert sent == []

    asyncio.run(run())


def test_cancelling_silence_during_claude_always_clears_in_flight(monkeypatch):
    """#13"""
    monkeypatch.setattr(coach, "SILENCE_S", 0.01)
    gate = asyncio.Event()
    claude_says(monkeypatch, "NONE", GOOD, gate=gate, gate_on=2)
    push_returns(monkeypatch, True)

    async def run():
        await coach.handle(body(other("We just adopted a rescue dog")))
        await until(in_flight)
        timer = coach.sessions[UID].silence_task
        timer.cancel()
        await asyncio.wait([timer])
        assert not coach.sessions[UID].llm_in_flight

    asyncio.run(run())
