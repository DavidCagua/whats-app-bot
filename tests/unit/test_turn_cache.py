"""
Unit tests for TurnCache and CatalogCache memoization layers.

These verify the core caching contracts without touching the DB:
session/customer lookups are deduped within a turn, invalidation drops
the entry so subsequent reads refetch, and contextvar isolation means
each begin_turn() hands out a fresh cache.
"""

import time
from unittest.mock import MagicMock

import pytest

from app.orchestration import turn_cache
from app.services import catalog_cache


# ────────────────────────────────────────────────────────────────────
# TurnCache
# ────────────────────────────────────────────────────────────────────


class TestTurnCacheSession:
    def test_first_call_invokes_loader(self):
        turn_cache.begin_turn()
        loader = MagicMock(return_value={"session": {"order_context": {}}})
        result = turn_cache.current().get_session("wa-1", "biz-1", loader=loader)
        assert result == {"session": {"order_context": {}}}
        assert loader.call_count == 1

    def test_second_call_returns_cached_without_invoking_loader(self):
        turn_cache.begin_turn()
        loader = MagicMock(return_value={"session": {"order_context": {"state": "GREETING"}}})
        turn_cache.current().get_session("wa-1", "biz-1", loader=loader)
        turn_cache.current().get_session("wa-1", "biz-1", loader=loader)
        assert loader.call_count == 1

    def test_different_keys_dont_collide(self):
        turn_cache.begin_turn()
        loader_a = MagicMock(return_value="A")
        loader_b = MagicMock(return_value="B")
        assert turn_cache.current().get_session("wa-1", "biz-1", loader=loader_a) == "A"
        assert turn_cache.current().get_session("wa-1", "biz-2", loader=loader_b) == "B"
        assert loader_a.call_count == 1
        assert loader_b.call_count == 1

    def test_invalidate_forces_refetch(self):
        turn_cache.begin_turn()
        loader = MagicMock(side_effect=[{"v": 1}, {"v": 2}])
        assert turn_cache.current().get_session("wa-1", "biz-1", loader=loader) == {"v": 1}
        turn_cache.current().invalidate_session("wa-1", "biz-1")
        assert turn_cache.current().get_session("wa-1", "biz-1", loader=loader) == {"v": 2}
        assert loader.call_count == 2

    def test_begin_turn_hands_out_fresh_cache(self):
        turn_cache.begin_turn()
        loader = MagicMock(return_value={"v": 1})
        turn_cache.current().get_session("wa-1", "biz-1", loader=loader)
        # New turn → cache is cleared, loader fires again on re-read
        turn_cache.begin_turn()
        turn_cache.current().get_session("wa-1", "biz-1", loader=loader)
        assert loader.call_count == 2


class TestTurnCacheCustomer:
    def test_set_customer_prepopulates_slot(self):
        turn_cache.begin_turn()
        turn_cache.current().set_customer("wa-1", {"name": "Laura"})
        loader = MagicMock()
        # Should hit the pre-populated slot, not the loader
        assert turn_cache.current().get_customer("wa-1", loader=loader) == {"name": "Laura"}
        assert loader.call_count == 0

    def test_loader_exception_caches_none(self):
        turn_cache.begin_turn()
        loader = MagicMock(side_effect=RuntimeError("db down"))
        assert turn_cache.current().get_customer("wa-1", loader=loader) is None
        # Second call should NOT retry the failing loader — cached None
        loader2 = MagicMock()
        assert turn_cache.current().get_customer("wa-1", loader=loader2) is None
        assert loader2.call_count == 0


# ────────────────────────────────────────────────────────────────────
# CatalogCache
# ────────────────────────────────────────────────────────────────────


class TestCatalogCache:
    def setup_method(self):
        catalog_cache.invalidate_all()

    def test_hit_and_miss(self):
        calls = []
        val = catalog_cache.get_or_fetch(
            "biz-1", "test", (), lambda: (calls.append(1), ["a", "b"])[1]
        )
        assert val == ["a", "b"]
        assert len(calls) == 1

        val2 = catalog_cache.get_or_fetch(
            "biz-1", "test", (), lambda: (calls.append(1), ["NEW"])[1]
        )
        assert val2 == ["a", "b"]  # cached
        assert len(calls) == 1

    def test_expiry(self):
        calls = []
        catalog_cache.get_or_fetch(
            "biz-1",
            "test",
            (),
            lambda: (calls.append(1), "v1")[1],
            ttl_seconds=0.01,
        )
        time.sleep(0.02)
        catalog_cache.get_or_fetch(
            "biz-1",
            "test",
            (),
            lambda: (calls.append(1), "v2")[1],
            ttl_seconds=0.01,
        )
        assert len(calls) == 2

    def test_invalidate_tenant_scoped(self):
        catalog_cache.get_or_fetch("biz-1", "test", (), lambda: "A")
        catalog_cache.get_or_fetch("biz-2", "test", (), lambda: "B")
        removed = catalog_cache.invalidate("biz-1")
        assert removed == 1

        # biz-1 refetches, biz-2 still cached
        calls = []
        catalog_cache.get_or_fetch(
            "biz-1", "test", (), lambda: (calls.append(1), "A2")[1]
        )
        catalog_cache.get_or_fetch(
            "biz-2", "test", (), lambda: (calls.append(1), "B2")[1]
        )
        assert len(calls) == 1  # biz-2 was cached

    def test_different_args_get_different_slots(self):
        calls = []
        catalog_cache.get_or_fetch(
            "biz-1", "list_products", ("burgers",), lambda: (calls.append(1), ["b"])[1]
        )
        catalog_cache.get_or_fetch(
            "biz-1", "list_products", ("drinks",), lambda: (calls.append(1), ["d"])[1]
        )
        assert len(calls) == 2
