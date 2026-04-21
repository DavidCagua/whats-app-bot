"""Unit tests for app/orchestration/conversation_manager.py — segment mapping, coalescing, fallback."""

from unittest.mock import patch, MagicMock

import pytest

from app.orchestration.conversation_manager import (
    _build_dispatch_segments,
    _coalesce,
    _resolve_primary_agent,
)
from app.orchestration import router


BIELA_CTX = {
    "business_id": "biela",
    "business": {"name": "Biela", "settings": {"conversation_primary_agent": "order"}},
}

ENABLED_AGENTS = [
    {"agent_type": "order", "priority": 1},
    {"agent_type": "customer_service", "priority": 2},
]


class TestResolvePrimaryAgent:
    def test_settings_primary_wins(self):
        assert _resolve_primary_agent(ENABLED_AGENTS, BIELA_CTX) == "order"

    def test_fallback_to_first_by_priority_when_primary_invalid(self):
        ctx = {"business": {"settings": {"conversation_primary_agent": "ghost"}}}
        assert _resolve_primary_agent(ENABLED_AGENTS, ctx) == "order"

    def test_no_primary_setting_uses_first(self):
        ctx = {"business": {"settings": {}}}
        assert _resolve_primary_agent(ENABLED_AGENTS, ctx) == "order"

    def test_no_enabled_agents_defaults_to_booking(self):
        assert _resolve_primary_agent([], BIELA_CTX) == "booking"


class TestCoalesce:
    def test_single_segment_unchanged(self):
        segs = [("order", "x")]
        assert _coalesce(segs) == [("order", "x")]

    def test_different_agents_not_coalesced(self):
        segs = [("order", "x"), ("customer_service", "y")]
        assert _coalesce(segs) == segs

    def test_consecutive_same_agent_merged(self):
        segs = [("order", "a"), ("order", "b"), ("customer_service", "c")]
        assert _coalesce(segs) == [("order", "a\nb"), ("customer_service", "c")]

    def test_noncontiguous_same_agent_not_merged(self):
        segs = [("order", "a"), ("customer_service", "b"), ("order", "c")]
        # Different domains between order segments → stays as-is.
        assert _coalesce(segs) == segs

    def test_empty_list_returns_empty(self):
        assert _coalesce([]) == []


class TestBuildDispatchSegments:
    def test_none_router_segments_runs_primary_on_full_message(self):
        result = _build_dispatch_segments(
            router_segments=None,
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="dame una barracuda",
        )
        assert result == [("order", "dame una barracuda")]

    def test_single_order_segment_passes_through(self):
        result = _build_dispatch_segments(
            router_segments=[(router.DOMAIN_ORDER, "dame una barracuda")],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="dame una barracuda",
        )
        assert result == [("order", "dame una barracuda")]

    def test_mixed_intent_maps_to_two_agents(self):
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_ORDER, "dame barracuda"),
                (router.DOMAIN_CUSTOMER_SERVICE, "a qué hora abren"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="dame barracuda y a qué hora abren",
        )
        assert result == [
            ("order", "dame barracuda"),
            ("customer_service", "a qué hora abren"),
        ]

    def test_catalog_domain_falls_back_to_primary(self):
        # No dedicated catalog agent — should fall back to order.
        result = _build_dispatch_segments(
            router_segments=[(router.DOMAIN_CATALOG, "qué bebidas tienen")],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="qué bebidas tienen",
        )
        assert result == [("order", "qué bebidas tienen")]

    def test_chat_domain_falls_back_to_primary(self):
        result = _build_dispatch_segments(
            router_segments=[(router.DOMAIN_CHAT, "ustedes hacen eventos")],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="ustedes hacen eventos",
        )
        assert result == [("order", "ustedes hacen eventos")]

    def test_customer_service_disabled_falls_back_to_primary(self):
        # customer_service mapped but not enabled for this business.
        result = _build_dispatch_segments(
            router_segments=[(router.DOMAIN_CUSTOMER_SERVICE, "a qué hora abren")],
            enabled_agents=[{"agent_type": "order", "priority": 1}],
            primary_agent_type="order",
            full_message="a qué hora abren",
        )
        assert result == [("order", "a qué hora abren")]

    def test_consecutive_catalog_and_order_coalesced_into_one_primary_call(self):
        # Router splits "qué tienen y dame una coca" into two segments,
        # both of which map to order (catalog → primary fallback).
        # Result: one coalesced order call instead of two.
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_CATALOG, "qué tienen"),
                (router.DOMAIN_ORDER, "dame una coca"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="qué tienen y dame una coca",
        )
        assert result == [("order", "qué tienen\ndame una coca")]

    def test_mixed_with_catalog_and_cs_keeps_separation(self):
        # catalog → order (fallback), customer_service → customer_service.
        # Two different targets, no coalescing.
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_CATALOG, "qué hay"),
                (router.DOMAIN_CUSTOMER_SERVICE, "a qué hora abren"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="qué hay y a qué hora abren",
        )
        assert result == [
            ("order", "qué hay"),
            ("customer_service", "a qué hora abren"),
        ]
