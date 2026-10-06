"""Convo Coach: live small-talk coaching on the Apple Watch, via an Omi realtime webhook.

Spec: omi_backend repo, docs/superpowers/specs/2026-10-05-convo-coach-design.md.
One process, asyncio, in-memory state per uid. No locks: asyncio is single-threaded,
so state changes between awaits are atomic, and Claude is always awaited outside them.
"""

import logging
import os
import re
import time

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI

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
    return "\n".join(f"{'USER' if seg.get('is_user') else 'OTHER'}: {seg.get('text', '')}" for _, _, seg in s.segments)


def validate(raw, recent):
    """Return the suggestion to deliver, or None. Strips one surrounding quote pair; never truncates."""
    text = raw.strip()
    if len(text) >= 2 and QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    if not text or text == "NONE" or "\n" in text:
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
