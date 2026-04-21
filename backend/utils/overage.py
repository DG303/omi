"""Usage-based overage billing for chat when users exceed their plan's included
question cap.

Experiment shape (backend-only):
  - Operator (PlanType.operator, desktop) has 500 included chat questions per month.
  - Past 500, the user is NOT blocked — calls go through and we accrue an
    overage charge equal to the true provider cost of the excess usage,
    marked up by ``OVERAGE_MARKUP_MULTIPLIER`` (default 15 %).
  - Neo (mobile) keeps its hard cap — no overage on mobile.
  - Architect stays on its monthly cost cap (hard-blocked past $400/mo).
  - Free users still get hard-capped (they have no payment method on file).
  - BYOK users bypass everything — handled by the existing BYOK check in
    ``utils.subscription.enforce_chat_quota``.

True costs are already tracked on every chat call via
``database.llm_usage.record_llm_usage_bucket`` → ``desktop_chat.cost_usd``.
This module reads those numbers rather than maintaining a parallel counter.
"""

import os
from typing import Optional

from database import user_usage as user_usage_db
from models.users import PlanType
from utils.subscription import OPERATOR_CHAT_QUESTIONS_PER_MONTH

# Markup applied to raw provider cost before charging the user.
# 1.15 = 15 % on top of true cost (covers variance + infra).
OVERAGE_MARKUP_MULTIPLIER = float(os.getenv('OVERAGE_MARKUP_MULTIPLIER', '1.15'))

# Per-1M-token reference rates shown in the explainer UI. These are NOT used
# for live computation — live cost is taken from the already-tracked
# `desktop_chat.cost_usd` which the LLM clients compute per-call from
# actual provider token counts. Rates here are purely informational.
PROVIDER_REFERENCE_RATES = {
    'claude_sonnet_input_per_mtok': 3.00,
    'claude_sonnet_output_per_mtok': 15.00,
    'gemini_flash_input_per_mtok': 0.30,
    'gemini_flash_output_per_mtok': 2.50,
    'gpt_4_1_mini_input_per_mtok': 0.40,
    'gpt_4_1_mini_output_per_mtok': 1.60,
    'deepgram_nova_per_min': 0.0043,
}

OVERAGE_EXPLAINER_TITLE = "What happens past your monthly limit?"

OVERAGE_EXPLAINER_BODY = (
    "Your plan includes {included_questions} chat questions per month. "
    "If you go over, Omi doesn't cut you off — you stay fully functional and we "
    "charge only for the extra usage, billed to the card on file at the end of your cycle.\n\n"
    "How the charge is computed:\n"
    "  • We add up the real provider cost (Claude, Gemini, Deepgram, etc.) of the "
    "questions you asked past {included_questions}.\n"
    "  • We add a {markup_pct:.0f}% buffer on top to cover infra and pricing variance.\n"
    "  • That's it — no surge pricing, no hidden fees.\n\n"
    "A typical chat question costs roughly $0.01–$0.05 of real compute. "
    "Heavy RAG questions with lots of tool use cost a bit more.\n\n"
    "Prefer predictable billing? You can always bring your own API keys in "
    "Settings → Developer API Keys and pay providers directly. When BYOK is "
    "active, Omi is free."
)


def build_explainer_text() -> str:
    return OVERAGE_EXPLAINER_BODY.format(
        included_questions=OPERATOR_CHAT_QUESTIONS_PER_MONTH,
        markup_pct=(OVERAGE_MARKUP_MULTIPLIER - 1.0) * 100.0,
    )


def _plan_included_questions(plan: PlanType) -> Optional[int]:
    """Number of chat questions included in the monthly fee for plans that
    participate in overage billing. Returns None if the plan is not eligible.

    Only Operator (the desktop mid-tier) participates in overage billing.
    Neo is hard-capped on mobile; Architect uses a monthly cost cap instead."""
    if plan == PlanType.operator:
        return OPERATOR_CHAT_QUESTIONS_PER_MONTH
    return None


def is_overage_plan(plan: PlanType) -> bool:
    """True if this plan uses overage billing past its included question count."""
    return _plan_included_questions(plan) is not None


def get_user_overage(uid: str, plan: PlanType) -> dict:
    """Compute the current-month overage snapshot for *uid* on *plan*.

    Returns a dict with:
      - included_questions: plan's included count (or None)
      - used_questions:     questions used this month
      - excess_questions:   max(0, used - included)
      - real_cost_usd:      provider cost for entire month (from tracked data)
      - overage_usd:        accrued overage charge with markup (0 if under cap)
      - markup_multiplier:  the multiplier applied
      - reset_at:           unix ts when the monthly bucket rolls over
    """
    included = _plan_included_questions(plan)
    usage = user_usage_db.get_monthly_chat_usage(uid)
    used = int(usage.get('questions', 0))
    real_cost = float(usage.get('cost_usd', 0.0))
    reset_at = usage.get('reset_at')

    overage_usd = 0.0
    excess = 0
    if included is not None and used > included and used > 0:
        excess = used - included
        # Attribute cost proportionally: the excess share of this month's real
        # cost is (excess / used) × total real cost, then apply markup.
        overage_usd = round((excess / used) * real_cost * OVERAGE_MARKUP_MULTIPLIER, 4)

    return {
        'included_questions': included,
        'used_questions': used,
        'excess_questions': excess,
        'real_cost_usd': round(real_cost, 4),
        'overage_usd': overage_usd,
        'markup_multiplier': OVERAGE_MARKUP_MULTIPLIER,
        'reset_at': reset_at,
    }
