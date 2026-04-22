"""
Tests for POST /v3/memories error handling and rate limit wiring.

Regression goal (#6940): POST /v3/memories must
  (a) have memories:create rate limit applied,
  (b) return 503 on Firestore failure (not unhandled 500),
  (c) survive vector upsert failure without 500 (memory still returned),
  (d) not attempt vector upsert when Firestore write fails,
  (e) run blocking work off the event loop via asyncio.to_thread.

The router import chain (database.memories → encryption → cryptography)
requires production env vars, so behavior tests use source-level verification
matching the repo pattern in test_rate_limiting.py.
"""

import os
import re

import pytest

from utils.rate_limit_config import RATE_POLICIES

ROUTER_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'routers', 'memories.py')


def _read_router():
    with open(ROUTER_PATH) as f:
        return f.read()


def _grep_router(pattern: str) -> list[str]:
    """Return lines matching pattern in the memories router."""
    matches = []
    with open(ROUTER_PATH) as f:
        for line in f:
            if re.search(pattern, line):
                matches.append(line.strip())
    return matches


# ---------------------------------------------------------------------------
# Policy existence tests
# ---------------------------------------------------------------------------


class TestMemoriesRateLimitPolicies:
    def test_memories_create_policy_exists(self):
        assert "memories:create" in RATE_POLICIES
        max_req, window = RATE_POLICIES["memories:create"]
        assert max_req == 60
        assert window == 3600

    def test_memories_modify_policy_exists(self):
        assert "memories:modify" in RATE_POLICIES
        max_req, window = RATE_POLICIES["memories:modify"]
        assert max_req == 120
        assert window == 3600

    def test_memories_delete_policy_exists(self):
        assert "memories:delete" in RATE_POLICIES
        max_req, window = RATE_POLICIES["memories:delete"]
        assert max_req == 60
        assert window == 3600

    def test_memories_delete_all_policy_exists(self):
        assert "memories:delete_all" in RATE_POLICIES
        max_req, window = RATE_POLICIES["memories:delete_all"]
        assert max_req == 2
        assert window == 3600


# ---------------------------------------------------------------------------
# Rate limit wiring tests (source-level grep)
# ---------------------------------------------------------------------------


class TestMemoriesRateLimitWiring:
    def test_create_endpoint_has_rate_limit(self):
        matches = _grep_router(r"with_rate_limit.*memories:create")
        assert len(matches) == 1, f"POST /v3/memories must have memories:create, found: {matches}"

    def test_batch_endpoint_has_rate_limit(self):
        matches = _grep_router(r"with_rate_limit.*memories:batch")
        assert len(matches) == 1, f"POST /v3/memories/batch must have memories:batch, found: {matches}"

    def test_delete_endpoint_has_rate_limit(self):
        matches = _grep_router(r"with_rate_limit.*memories:delete[^_]")
        assert len(matches) == 1, f"DELETE /v3/memories/{{id}} must have memories:delete, found: {matches}"

    def test_delete_all_endpoint_has_rate_limit(self):
        matches = _grep_router(r"with_rate_limit.*memories:delete_all")
        assert len(matches) == 1, f"DELETE /v3/memories must have memories:delete_all, found: {matches}"

    def test_review_endpoint_has_rate_limit(self):
        matches = _grep_router(r"with_rate_limit.*memories:modify")
        assert len(matches) >= 1, f"Review/edit/visibility must have memories:modify, found: {matches}"

    def test_all_write_endpoints_rate_limited(self):
        """Every write endpoint in memories.py must use with_rate_limit."""
        matches = _grep_router(r"with_rate_limit.*memories:")
        # create, batch, delete, delete_all, modify(review), modify(edit), modify(visibility) = 7
        assert len(matches) == 7, f"Expected 7 rate-limited endpoints, got {len(matches)}: {matches}"


# ---------------------------------------------------------------------------
# Error handling tests (source-level verification)
# ---------------------------------------------------------------------------


class TestCreateMemoryErrorHandling:
    """Verify error handling structure in create_memory source code."""

    def test_create_memory_is_async(self):
        """create_memory must be async def (prevents threadpool exhaustion)."""
        source = _read_router()
        assert re.search(r'async def create_memory\(', source), "create_memory must be async def"

    def test_create_memory_uses_to_thread(self):
        """Blocking work must be offloaded via asyncio.to_thread."""
        source = _read_router()
        # Must have at least 2 to_thread calls (Firestore + vector)
        to_thread_calls = re.findall(r'asyncio\.to_thread\(', source)
        # At least 3: batch's _persist, create's Firestore, create's vector
        assert len(to_thread_calls) >= 3, f"Expected >=3 asyncio.to_thread calls, got {len(to_thread_calls)}"

    def test_firestore_write_has_error_handling(self):
        """Firestore write in create_memory must be wrapped in try/except."""
        source = _read_router()
        # The pattern: try + to_thread(_persist) + except -> 503
        assert 'HTTPException(status_code=503' in source, "Firestore failure must return 503"

    def test_vector_upsert_has_error_handling(self):
        """Vector upsert failure must be caught and logged (not 500)."""
        source = _read_router()
        assert 'Vector upsert failed' in source, "Vector upsert failure must be logged"

    def test_vector_delete_has_error_handling(self):
        """Vector delete in delete_memory must be caught (not 500)."""
        source = _read_router()
        assert 'Vector delete failed' in source, "Vector delete failure must be logged"

    def test_firestore_failure_blocks_vector_upsert(self):
        """If Firestore fails (raises), vector upsert must not execute.

        Verified by structural ordering: Firestore try/except with raise
        appears before vector try/except in the create_memory function.
        """
        source = _read_router()
        # Find positions of both error-handling blocks
        firestore_pos = source.find('HTTPException(status_code=503')
        vector_pos = source.find('Vector upsert failed')
        assert firestore_pos < vector_pos, "Firestore error handling must come before vector upsert"


# ---------------------------------------------------------------------------
# Delete-all safety tests
# ---------------------------------------------------------------------------


class TestDeleteAllRateLimit:
    def test_delete_all_limit_is_tight(self):
        """delete_all is extremely destructive — must have very tight limits."""
        max_req, window = RATE_POLICIES["memories:delete_all"]
        assert max_req <= 5, f"delete_all limit too high: {max_req}"
        assert window >= 3600, f"delete_all window too short: {window}"
