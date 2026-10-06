"""Convo Coach: live small-talk coaching on the Apple Watch, via an Omi realtime webhook.

Spec: omi_backend repo, docs/superpowers/specs/2026-10-05-convo-coach-design.md.
One process, asyncio, in-memory state per uid. No locks: asyncio is single-threaded,
so state changes between awaits are atomic, and Claude is always awaited outside them.
"""

import asyncio
import logging
import os
import re
import time

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("convo-coach")

BUFFER_S = 120
COOLDOWN_S = 45
SILENCE_S = 8
OPENER_TTL_S = 30
MAX_WORDS = 12
MODEL = "claude-haiku-4-5-20251001"
OMI_API_URL = os.getenv("OMI_API_URL", "http://backend:8080")
# Whole phrase, matched after normalize(); covers the usual mis-transcriptions of "omi"
CODEWORD = re.compile(r"\b(?:omi|oh me|omni|oh my) opener\b")
NONE = re.compile(r"NONE\b")  # the model sometimes appends an explanation
QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”"}

# Paraphrased from Vanessa Van Edwards' public material; no book text.
SHARED_RULES = """Rules:
- Output exactly one question or line, 12 words max. No preamble, no quotes, no explanation.
- Output exactly NONE when nothing is worth saying.
- Invite a story or an opinion. Never a yes/no question. Skip autopilot questions like "How are you", "What do you do", "Busy week?".
- Be conversational, not interrogating: not investigative, not hyper-specific.
- Use only what was actually said. Never use names or details from speaker labels or metadata."""

PROMPTS = {
    "opener": (
        "You're helping someone who finds small talk hard approach a person. "
        "The input is their quick spoken note about the setting. Write one natural opening line "
        "tied to the setting or something visible. It must include an open question. If the "
        "context is empty, give a strong all-purpose opener. No appearance compliments, nothing "
        "that sounds like a pickup line."
    ),
    "followup": (
        "You're quietly coaching USER through a conversation; OTHER is anyone else. Find the most "
        "recent unresolved hook OTHER introduced: a place, project, decision, person, or emotion. "
        "Write one open follow-up USER could ask next. Do not ask for private or sensitive details "
        "unless OTHER clearly introduced them and invited more. Output NONE if the hook was already "
        "followed up, USER is mid-story, it's unclear who said it, or nothing is worth following."
    ),
    "restart": (
        "You're quietly coaching USER through a conversation; OTHER is anyone else. There's been a "
        "pause. If a restart is warranted, suggest one question returning to a light or positive "
        "earlier thread OTHER introduced, or a light topic tied to the setting. Never reopen "
        "sensitive, negative, or deeply personal topics. Output NONE if the pause seems natural or "
        "the conversation has ended (goodbyes, \"nice meeting you\")."
    ),
}

app = FastAPI()
sessions = {}
_claude = None


class Session:
    def __init__(self):
        self.segments = []  # (received_at, seq, segment dict), trimmed to BUFFER_S
        self.seq = 0  # segments ever received; never shrinks, so trimming can't shift it
        self.last_checked = 0  # highest seq the LLM gate has already considered
        self.cooldown_until = 0.0
        self.opener_armed_at = None
        self.opener_context = ""
        self.llm_in_flight = False
        self.last_suggestions = []
        self.silence_task = None


def normalize(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def opener_live(s):
    return s.opener_armed_at is not None and time.monotonic() - s.opener_armed_at < OPENER_TTL_S


def ingest(s, segments):
    """Append segments, arm the opener on the codeword, collect its context, trim to BUFFER_S."""
    now = time.monotonic()
    for seg in segments:
        s.seq += 1
        s.segments.append((now, s.seq, seg))
        if not seg.get("is_user"):
            continue
        norm = normalize(seg.get("text") or "")
        match = CODEWORD.search(norm)
        if match:
            s.opener_armed_at = now
            s.opener_context = norm[match.end() :].strip()
        elif opener_live(s):
            s.opener_context = f"{s.opener_context} {norm}".strip()
    s.segments = [x for x in s.segments if now - x[0] <= BUFFER_S]


def new_other_speech(s):
    return any(seq > s.last_checked and not seg.get("is_user") for _, seq, seg in s.segments)


def transcript(s):
    # Labels only; never speaker names or person ids
    return "\n".join(
        f"{'USER' if seg.get('is_user') else 'OTHER'}: {seg.get('text') or ''}" for _, _, seg in s.segments
    )


def validate(raw, recent):
    """Return the suggestion to deliver, or None. Strips one surrounding quote pair; never truncates."""
    text = raw.strip()
    if len(text) >= 2 and QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    if not text or NONE.match(text) or "\n" in text:
        return None
    # Omi drops messages of 5 chars or fewer, so a cooldown for one would be wasted
    if len(text) <= 5 or len(text.split()) > MAX_WORDS or text in recent:
        return None
    return text


async def ask_claude(mode, user_input, recent):
    """One Haiku call with a 4s timeout and no retries: a late suggestion is worthless."""
    global _claude
    if _claude is None:  # lazy: the client refuses to construct without ANTHROPIC_API_KEY
        _claude = AsyncAnthropic(timeout=4.0, max_retries=0)
    system = PROMPTS[mode] + "\n\n" + SHARED_RULES
    if recent:
        system += "\n- Never repeat any of these earlier suggestions: " + " | ".join(recent)
    resp = await _claude.messages.create(
        model=MODEL,
        max_tokens=60,
        system=system,
        messages=[{"role": "user", "content": user_input}],
    )
    return resp.content[0].text


def deliver(s, text):
    """A suggestion went out: start the cooldown and remember it so prompts don't repeat it."""
    s.cooldown_until = time.monotonic() + COOLDOWN_S
    s.last_suggestions = (s.last_suggestions + [text])[-3:]


async def run_claude(s, mode):
    """One guarded Claude call. Returns a validated line or None; llm_in_flight always clears."""
    s.llm_in_flight = True
    started = time.monotonic()
    result = "error"
    text = None
    try:
        if mode == "opener":
            user_input = f"Context: {s.opener_context}"
        else:
            user_input = "Transcript, oldest first:\n" + transcript(s)
        raw = await ask_claude(mode, user_input, s.last_suggestions)
        text = validate(raw, s.last_suggestions)
        result = "fired" if text else "none" if NONE.match(raw.strip()) else "dropped"
    except asyncio.CancelledError:
        result = "cancelled"
        raise
    except Exception as e:  # timeout, API error: no retry, a late suggestion is worthless
        log.warning("claude error: %s", type(e).__name__)
    finally:
        s.llm_in_flight = False
        log.info("mode=%s result=%s latency_ms=%d", mode, result, (time.monotonic() - started) * 1000)
    return text


async def evaluate(s):
    """Webhook decision. If Claude says nothing but speech arrived during the call,
    look once more in this same request, so that speech isn't stranded."""
    for _ in range(2):
        if s.llm_in_flight:
            return {}
        if opener_live(s) and s.opener_context:
            mode = "opener"
        elif new_other_speech(s) and time.monotonic() >= s.cooldown_until:
            mode = "followup"
        else:
            return {}
        snapshot = s.seq
        text = await run_claude(s, mode)
        s.last_checked = max(s.last_checked, snapshot)
        if mode == "opener":
            s.opener_armed_at = None
        if text:
            deliver(s, text)
            return {"message": text}
        if s.seq == snapshot:
            return {}
    return {}


async def handle(body):
    uid = body.get("session_id")
    segments = body.get("segments")
    if not uid or not segments:
        return {}
    s = sessions.setdefault(uid, Session())
    ingest(s, segments)
    await reschedule_silence(s, uid)
    return await evaluate(s)


@app.post("/webhook")
async def webhook(request: Request):
    # Always 200: an error status trips Omi's per-URL circuit breaker and the coach goes quiet
    try:
        return await handle(await request.json())
    except Exception as e:
        log.error("webhook error: %s", type(e).__name__)
        return {}


async def push(uid, text):
    """App-initiated push through the Omi backend. Rate-limited there to 10/hour."""
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(
            f"{OMI_API_URL}/v1/integrations/notification",
            headers={"Authorization": f"Bearer {os.environ['OMI_APP_API_KEY']}"},
            json={"aid": os.environ["OMI_APP_ID"], "uid": uid, "message": text},
        )
    # The error body is the backend's detail string (bad key, not installed, 429), no transcript
    log.info("push status=%d%s", r.status_code, "" if r.is_success else f" detail={r.text[:200]}")
    return r.is_success


async def silence(s, uid):
    """Fires once, SILENCE_S after the last segment, unless new speech cancels it first."""
    await asyncio.sleep(SILENCE_S)
    try:
        if s.llm_in_flight or time.monotonic() < s.cooldown_until:
            return
        mode = "opener" if opener_live(s) else "restart"
        text = await run_claude(s, mode)
        if mode == "opener":
            s.opener_armed_at = None
        if text:
            # Deliver before pushing: a cancel mid-push may still send it, so keep the cooldown.
            # Roll back only if the push definitely failed (False or an Exception, not a cancel).
            before = (s.cooldown_until, s.last_suggestions)
            deliver(s, text)
            try:
                ok = await push(uid, text)
            except Exception:
                s.cooldown_until, s.last_suggestions = before
                raise
            if not ok:
                s.cooldown_until, s.last_suggestions = before
    except Exception as e:
        log.warning("silence error: %s", type(e).__name__)


async def reschedule_silence(s, uid):
    # Webhooks are serialized per pusher connection, not per uid, so two can overlap here.
    # Swap in the new timer before awaiting (it sleeps SILENCE_S before touching state), so a
    # concurrent call sees it and cancels it instead of orphaning it.
    old, s.silence_task = s.silence_task, asyncio.create_task(silence(s, uid))
    if old and not old.done():
        old.cancel()
        # Wait for it to unwind so its finally clears llm_in_flight before we evaluate
        await asyncio.wait([old])
