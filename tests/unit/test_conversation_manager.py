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

    def test_browsing_classifies_as_order(self):
        # Browsing the menu inside the bot is part of the "order" user
        # concern (see docs/agents-vs-services.md). Router should emit
        # `order` for "qué bebidas tienen", not a separate catalog domain.
        result = _build_dispatch_segments(
            router_segments=[(router.DOMAIN_ORDER, "qué bebidas tienen")],
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

    def test_repeated_same_router_domain_still_coalesces(self):
        # Router over-decomposing a single-domain message → coalesce.
        # Both segments are the SAME router domain (order) so they
        # represent one logical intent the agent's planner can handle.
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_ORDER, "una coca"),
                (router.DOMAIN_ORDER, "y una pepsi"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="una coca y una pepsi",
        )
        assert result == [("order", "una coca\ny una pepsi")]

    def test_browse_plus_cs_link_request_keeps_separation(self):
        # The bug from 2026-04-25 reframed under the new domain layout:
        # "envíame la carta" is now classified as customer_service (the
        # menu URL is a business asset, not a browse action). "y dame una
        # barracuda" is order. Different domains → kept separate so each
        # agent runs with its proper segment, composer merges the replies.
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_CUSTOMER_SERVICE, "envíame la carta"),
                (router.DOMAIN_ORDER, "y dame una barracuda"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="envíame la carta y dame una barracuda",
        )
        assert result == [
            ("customer_service", "envíame la carta"),
            ("order", "y dame una barracuda"),
        ]

    def test_browse_then_order_in_same_concern_coalesces(self):
        # Repeated `order` domain (over-decomposed by router): both belong
        # to the same user concern (ordering), coalesce into one call.
        result = _build_dispatch_segments(
            router_segments=[
                (router.DOMAIN_ORDER, "qué bebidas tienen"),
                (router.DOMAIN_ORDER, "y dame una coca"),
            ],
            enabled_agents=ENABLED_AGENTS,
            primary_agent_type="order",
            full_message="qué bebidas tienen y dame una coca",
        )
        assert result == [("order", "qué bebidas tienen\ny dame una coca")]
