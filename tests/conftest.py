"""
Shared fixtures for all test layers.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


# Reset the per-turn memoization cache between tests. It lives in a
# contextvar and would otherwise carry state across tests that share a
# worker thread, producing spooky cross-test pollution (cart items from
# a prior test leaking into the next one's _cart_from_session view).
@pytest.fixture(autouse=True)
def _reset_turn_cache():
    from app.orchestration import turn_cache
    turn_cache.begin_turn()
    yield


# ---------------------------------------------------------------------------
# Fake session state service (in-memory, no DB)
# ---------------------------------------------------------------------------

class FakeSessionStateService:
    """In-memory replacement for SessionStateService. No DB needed."""

    def __init__(self):
        self._store = {}  # key: (wa_id, business_id) -> session dict

    def load(self, wa_id, business_id, timeout_minutes=None):
        key = (wa_id, str(business_id))
        if key not in self._store:
            session = {
                "active_agents": [],
                "order_context": {"state": "GREETING"},
                "booking_context": {},
                "agent_contexts": {},
                "last_order_id": None,
                "last_booking_id": None,
            }
            return {"session": session, "is_new": True, "is_expired": False}
        return {"session": self._store[key], "is_new": False, "is_expired": False}

    def save(self, wa_id, business_id, state_update):
        key = (wa_id, str(business_id))
        existing = self._store.get(key, {
            "active_agents": [],
            "order_context": {},
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        })
        if "active_agents" in state_update:
            existing["active_agents"] = state_update["active_agents"]
        if "order_context" in state_update:
            val = state_update["order_context"]
            if val is None:
                existing["order_context"] = {}
            elif isinstance(val, dict):
                existing["order_context"] = {**existing.get("order_context", {}), **val}
        if "last_order_id" in state_update:
            existing["last_order_id"] = state_update["last_order_id"]
        self._store[key] = existing

    def reset(self):
        self._store.clear()


@pytest.fixture
def fake_session():
    """Provides a clean FakeSessionStateService per test."""
    return FakeSessionStateService()


# ---------------------------------------------------------------------------
# Test business context
# ---------------------------------------------------------------------------

FAKE_BUSINESS_ID = "00000000-0000-0000-0000-000000000001"
FAKE_WA_ID = "573001234567"

@pytest.fixture
def business_context():
    """Minimal business context for testing."""
    return {
        "business_id": FAKE_BUSINESS_ID,
        "business": {
            "name": "Test Restaurant",
            "settings": {
                "menu_url": "https://example.com/menu",
                "delivery_fee": 5000,
                "products_enabled": True,
            },
        },
    }


@pytest.fixture
def wa_id():
    return FAKE_WA_ID


# ---------------------------------------------------------------------------
# Sample products (for mocking product_order_service)
# ---------------------------------------------------------------------------

SAMPLE_PRODUCTS = [
    {
        "id": "prod-001",
        "name": "BARRACUDA",
        "price": 18000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa doble carne, queso cheddar, tocineta, cebolla caramelizada",
        "is_active": True,
    },
    {
        "id": "prod-002",
        "name": "COCA COLA",
        "price": 5000,
        "currency": "COP",
        "category": "BEBIDAS",
        "description": "Coca Cola 400ml",
        "is_active": True,
    },
    {
        "id": "prod-003",
        "name": "MONTESA",
        "price": 20000,
        "currency": "COP",
        "category": "HAMBURGUESAS",
        "description": "Hamburguesa con queso azul, champiñones, cebolla crispy",
        "is_active": True,
    },
]

@pytest.fixture
def sample_products():
    return SAMPLE_PRODUCTS
