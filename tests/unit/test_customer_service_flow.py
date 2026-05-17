"""
Unit tests for app/orchestration/customer_service_flow.py.

The CS agent calls these handlers via @tool wrappers in
app/services/cs_tools.py; these tests target the handlers directly so we
keep coverage of the business logic regardless of how the agent layer
dispatches them.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.orchestration import customer_service_flow as csf


# Intent labels used only by these tests (the legacy CS planner emitted
# them; the agent no longer does). Kept local so we don't reintroduce
# them as module-level exports.
INTENT_GET_BUSINESS_INFO = "GET_BUSINESS_INFO"
INTENT_GET_ORDER_STATUS = "GET_ORDER_STATUS"
INTENT_GET_ORDER_HISTORY = "GET_ORDER_HISTORY"
INTENT_SELECT_LISTED_PROMO = "SELECT_LISTED_PROMO"


def _recent_iso(minutes_ago: int = 1) -> str:
    """An ISO timestamp `minutes_ago` minutes in the past, UTC."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture(autouse=True)
def _stable_handoff_threshold(monkeypatch):
    """
    Pin the delivery-handoff threshold to 50 minutes for every test. Local
    .env files often override DELIVERY_HANDOFF_THRESHOLD_MIN to 1 for manual
    testing; without this fixture the threshold leaks into pytest and flips
    "recent" orders into auto-handoff territory.
    """
    monkeypatch.setattr(csf, "_delivery_handoff_threshold_min", lambda: 50)


WA = "+573001234567"
BIZ = "biz-1"
BIZ_CTX = {"business_id": BIZ, "business": {"name": "Biela", "settings": {"hours_text": "Lun-Vie 5PM"}}}


def _run(intent, params=None, ctx=BIZ_CTX, session=None):
    """
    Dispatch to the handler that backs ``intent``. Mirrors the routing
    the legacy ``execute_customer_service_intent`` did, so existing test
    bodies keep working unchanged.
    """
    params = params or {}
    if intent == INTENT_GET_BUSINESS_INFO:
        return csf._handle_business_info(WA, BIZ, ctx, params, session)
    if intent == INTENT_GET_ORDER_STATUS:
        return csf._handle_order_status(WA, BIZ, session)
    if intent == INTENT_GET_ORDER_HISTORY:
        return csf._handle_order_history(WA, BIZ, params)
    if intent == INTENT_SELECT_LISTED_PROMO:
        return csf._handle_select_listed_promo(WA, BIZ, params, session)
    raise AssertionError(f"unknown intent in test: {intent!r}")


def _session_with_prior_status_ask(order_id: str, count: int = 1):
    """Session shape that simulates `count` prior GET_ORDER_STATUS turns for the order."""
    return {
        "agent_contexts": {
            "customer_service": {
                "last_status_order_id": order_id,
                "last_status_ask_count": count,
            }
        }
    }


class TestGetBusinessInfo:
    def test_found_field_returns_business_info_result(self):
        result = _run(INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        assert result["result_kind"] == csf.RESULT_KIND_BUSINESS_INFO
        assert result["success"] is True
        assert result["field"] == "hours"
        assert result["value"] == "Lun-Vie 5PM"

    def test_unknown_field_returns_info_missing(self):
        result = _run(INTENT_GET_BUSINESS_INFO, {"field": "floor_plan"})
        assert result["result_kind"] == csf.RESULT_KIND_INFO_MISSING
        assert result["field"] == "floor_plan"
        assert "hours" in result["available_fields"]

    def test_missing_field_in_params_returns_info_missing(self):
        result = _run(INTENT_GET_BUSINESS_INFO, {})
        assert result["result_kind"] == csf.RESULT_KIND_INFO_MISSING
        assert result["field"] is None

    def test_field_not_configured_returns_info_missing(self):
        ctx = {"business_id": BIZ, "business": {"name": "X", "settings": {}}}
        result = _run(INTENT_GET_BUSINESS_INFO, {"field": "hours"}, ctx=ctx)
        assert result["result_kind"] == csf.RESULT_KIND_INFO_MISSING

    def test_hours_response_prepends_open_status_when_open(self):
        # Simulate a populated availability table that puts the business
        # currently open. The schedule string + a leading "Sí, estamos
        # abiertos" sentence must both appear in the value.
        with patch(
            "app.services.business_info_service._get_hours_for_business",
            return_value="Lun a Vie: 5:00 PM - 10:00 PM",
        ), patch(
            "app.services.business_info_service.compute_open_status",
            return_value={
                "has_data": True,
                "is_open": True,
                "closes_at": __import__("datetime").time(22, 0),
                "opens_at": None,
                "next_open_dow": None,
                "next_open_time": None,
                "now_local": None,
            },
        ):
            result = _run(INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        assert result["result_kind"] == csf.RESULT_KIND_BUSINESS_INFO
        value = result["value"]
        assert "Sí, estamos abiertos" in value
        assert "Lun a Vie: 5:00 PM - 10:00 PM" in value

    def test_hours_response_prepends_open_status_when_closed(self):
        # Closed before opening today — must produce the
        # "Hoy abrimos a las..." sentence.
        from datetime import time as t, datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            pytest.skip("zoneinfo unavailable")
        now_tue_1445 = datetime.fromisoformat("2026-05-05T14:45:00").replace(
            tzinfo=ZoneInfo("America/Bogota"))
        with patch(
            "app.services.business_info_service._get_hours_for_business",
            return_value="Lun a Vie: 5:00 PM - 10:00 PM",
        ), patch(
            "app.services.business_info_service.compute_open_status",
            return_value={
                "has_data": True,
                "is_open": False,
                "closes_at": None,
                "opens_at": t(17, 0),
                "next_open_dow": 2,  # Tuesday
                "next_open_time": t(17, 0),
                "now_local": now_tue_1445,
            },
        ):
            result = _run(INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        value = result["value"]
        assert "Por ahora estamos cerrados" in value
        assert "Hoy abrimos a las 5:00 PM" in value
        assert "Lun a Vie: 5:00 PM - 10:00 PM" in value

    def test_delivery_time_pickup_session_returns_pickup_range(self):
        """
        Regression: production observation 2026-05-17 (Biela / +57…).
        Pickup user mid-checkout asked "en cuánto puedo pasar?". No
        order placed yet, so the per-order ETA path didn't fire. The
        handler used to fall through to the generic delivery_time text
        ("45 minutos") which is wrong for pickup (no last-mile leg).
        Now: session.fulfillment_type=pickup → PICKUP_RANGE_TEXT with
        a pickup-specific field key the renderer maps to its own
        template.
        """
        from app.services.order_eta import PICKUP_RANGE_TEXT
        session = {"order_context": {"fulfillment_type": "pickup", "items": [{}]}}
        with patch.object(
            csf.order_lookup_service, "get_latest_order", return_value=None,
        ):
            result = _run(
                INTENT_GET_BUSINESS_INFO,
                {"field": "delivery_time"},
                session=session,
            )
        assert result["result_kind"] == csf.RESULT_KIND_BUSINESS_INFO
        assert result["field"] == "pickup_time"
        assert result["value"] == PICKUP_RANGE_TEXT

    def test_delivery_time_delivery_session_returns_generic(self):
        """
        Sanity: delivery-mode session (or no session) keeps the generic
        delivery_time text. The pickup swap must not affect delivery.
        """
        ctx = {
            "business_id": BIZ,
            "business": {"name": "X", "settings": {"delivery_time_text": "40 a 50 minutos"}},
        }
        session = {"order_context": {"fulfillment_type": "delivery"}}
        with patch.object(
            csf.order_lookup_service, "get_latest_order", return_value=None,
        ):
            result = _run(
                INTENT_GET_BUSINESS_INFO,
                {"field": "delivery_time"},
                ctx=ctx,
                session=session,
            )
        assert result["result_kind"] == csf.RESULT_KIND_BUSINESS_INFO
        assert result["field"] == "delivery_time"
        assert result["value"] == "40 a 50 minutos"

    def test_delivery_time_no_session_returns_generic(self):
        # No session at all (e.g. cold turn) → falls through normally.
        ctx = {
            "business_id": BIZ,
            "business": {"name": "X", "settings": {"delivery_time_text": "40 a 50 minutos"}},
        }
        with patch.object(
            csf.order_lookup_service, "get_latest_order", return_value=None,
        ):
            result = _run(
                INTENT_GET_BUSINESS_INFO,
                {"field": "delivery_time"},
                ctx=ctx,
                session=None,
            )
        assert result["field"] == "delivery_time"
        assert result["value"] == "40 a 50 minutos"

    def test_hours_response_no_open_status_when_no_availability_data(self):
        # No availability rows → no sentence prepended; value is just
        # the hours string (legacy fallback path).
        with patch(
            "app.services.business_info_service._get_hours_for_business",
            return_value=None,  # falls back to settings.hours_text
        ), patch(
            "app.services.business_info_service.compute_open_status",
            return_value={
                "has_data": False, "is_open": False,
                "closes_at": None, "opens_at": None,
                "next_open_dow": None, "next_open_time": None,
                "now_local": None,
            },
        ):
            result = _run(INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        value = result["value"]
        assert value == "Lun-Vie 5PM"  # the BIZ_CTX settings fallback
        assert "estamos abiertos" not in value
        assert "estamos cerrados" not in value


class TestGetOrderStatus:
    def test_existing_order(self):
        # Recent created_at: stays under the delivery-handoff threshold,
        # so the normal status path runs.
        order = {
            "id": "o1", "status": "pending", "total_amount": 25000,
            "delivery_address": "Cra 7", "payment_method": "nequi",
            "notes": None, "created_at": _recent_iso(minutes_ago=1),
            "items": [{"quantity": 2, "unit_price": 12500}],
        }
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order):
            result = _run(INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        assert result["order"]["status"] == "pending"
        assert result["order"]["total_amount"] == 25000
        assert result["order"]["items"] == [{"quantity": 2, "unit_price": 12500}]

    def test_no_order_found(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            result = _run(INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    # test_lookup_exception_returns_internal_error removed: the catch-all
    # that translated handler exceptions into RESULT_KIND_INTERNAL_ERROR
    # lived in the deleted execute_customer_service_intent dispatcher.
    # Exception handling now lives in the agent's tool dispatch loop
    # ([customer_service_agent.py] try/except around tool_fn.invoke).


class TestDeliveryHandoff:
    """Handoff fires only on the SECOND status ask for the same order, past 50min."""

    def _aged_order(self, status: str, minutes_ago: int, order_id: str = "o1"):
        return {
            "id": order_id, "status": status, "total_amount": 25000,
            "delivery_address": "Cra 7", "payment_method": "nequi",
            "notes": None, "created_at": _recent_iso(minutes_ago=minutes_ago),
            "items": [{"quantity": 1, "unit_price": 25000}],
        }

    @pytest.mark.parametrize("status", ["pending", "confirmed", "out_for_delivery"])
    def test_second_ask_past_threshold_triggers_handoff_and_disables_agent(self, status):
        order = self._aged_order(status, minutes_ago=60)
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled") as m_disable:
            result = _run(
                INTENT_GET_ORDER_STATUS,
                session=_session_with_prior_status_ask("o1", count=1),
            )
        assert result["result_kind"] == csf.RESULT_KIND_DELIVERY_HANDOFF
        assert result["order"]["status"] == status
        assert result["state_patch"]["last_status_order_id"] == "o1"
        assert result["state_patch"]["last_status_ask_count"] == 2
        m_disable.assert_called_once_with(
            BIZ, WA, False, handoff_reason="delivery_handoff",
        )

    def test_first_ask_past_threshold_returns_status_not_handoff(self):
        # No prior ask in session → first time the customer asks about this
        # order. Even past 50min, we give a normal status reply and bump the
        # counter so the NEXT ask escalates.
        order = self._aged_order("out_for_delivery", minutes_ago=70)
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled") as m_disable:
            result = _run(INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        assert result["state_patch"]["last_status_order_id"] == "o1"
        assert result["state_patch"]["last_status_ask_count"] == 1
        m_disable.assert_not_called()

    def test_counter_resets_for_new_order_id(self):
        # The session remembers a prior ask for order "old", but the customer
        # has since placed a new order "new". The new order is past 50min;
        # the prior counter must NOT carry over to a different order id.
        order = self._aged_order("out_for_delivery", minutes_ago=80, order_id="new")
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled") as m_disable:
            result = _run(
                INTENT_GET_ORDER_STATUS,
                session=_session_with_prior_status_ask("old", count=3),
            )
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        assert result["state_patch"]["last_status_order_id"] == "new"
        assert result["state_patch"]["last_status_ask_count"] == 1
        m_disable.assert_not_called()

    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    def test_terminal_status_never_triggers_handoff(self, status):
        order = self._aged_order(status, minutes_ago=120)
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled") as m_disable:
            result = _run(
                INTENT_GET_ORDER_STATUS,
                session=_session_with_prior_status_ask("o1", count=5),
            )
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        m_disable.assert_not_called()

    def test_under_threshold_does_not_trigger_handoff(self):
        # Even with many prior asks, an order under the threshold stays
        # in normal status mode.
        order = self._aged_order("confirmed", minutes_ago=10)
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled") as m_disable:
            result = _run(
                INTENT_GET_ORDER_STATUS,
                session=_session_with_prior_status_ask("o1", count=4),
            )
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        m_disable.assert_not_called()

    def test_disable_failure_still_returns_handoff(self):
        # If the kill-switch write fails on the qualifying ask, we must
        # still surface the apology message rather than fall back to a
        # normal status reply.
        order = self._aged_order("out_for_delivery", minutes_ago=70)
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order), \
             patch.object(csf.conversation_agent_service, "set_agent_enabled",
                          side_effect=RuntimeError("db down")):
            result = _run(
                INTENT_GET_ORDER_STATUS,
                session=_session_with_prior_status_ask("o1", count=1),
            )
        assert result["result_kind"] == csf.RESULT_KIND_DELIVERY_HANDOFF


class TestGetOrderStatusActiveCartHandoff:
    """The 'mi pedido' ambiguity: CS should defer to order when cart is active."""

    def _run_with_session(self, session):
        return csf._handle_order_status(WA, BIZ, session)

    def test_active_cart_triggers_handoff_to_order(self):
        session = {"order_context": {"items": [{"name": "Barracuda", "quantity": 1}]}}
        # Ensure no DB lookup happens when the handoff fires — the guard
        # must short-circuit BEFORE order_lookup_service.
        with patch.object(csf.order_lookup_service, "get_latest_order") as m:
            result = self._run_with_session(session)
            m.assert_not_called()
        assert result["result_kind"] == csf.RESULT_KIND_HANDOFF
        assert result["handoff"]["to"] == "order"
        assert result["handoff"]["context"]["reason"] == "mi_pedido_active_cart"

    def test_empty_cart_falls_through_to_lookup(self):
        session = {"order_context": {"items": []}}
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            result = self._run_with_session(session)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_no_session_falls_through_to_lookup(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            result = self._run_with_session(None)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_missing_order_context_key_falls_through(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            result = self._run_with_session({})
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER


class TestGetOrderHistory:
    def test_returns_orders_cleaned(self):
        raw = [
            {"id": "o1", "status": "completed", "total_amount": 20000, "items": []},
            {"id": "o2", "status": "pending", "total_amount": 15000, "items": []},
        ]
        with patch.object(csf.order_lookup_service, "get_order_history", return_value=raw):
            result = _run(INTENT_GET_ORDER_HISTORY)
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_HISTORY
        assert len(result["orders"]) == 2
        # Clean shape
        assert set(result["orders"][0].keys()) >= {"id", "status", "total_amount", "items"}

    def test_no_history_returns_no_order(self):
        with patch.object(csf.order_lookup_service, "get_order_history", return_value=[]):
            result = _run(INTENT_GET_ORDER_HISTORY)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_limit_clamped_to_20(self):
        with patch.object(
            csf.order_lookup_service, "get_order_history", return_value=[],
        ) as m:
            _run(INTENT_GET_ORDER_HISTORY, {"limit": 999})
        m.assert_called_once()
        _, kwargs = m.call_args
        assert kwargs["limit"] == 20

    def test_invalid_limit_defaults_to_5(self):
        with patch.object(
            csf.order_lookup_service, "get_order_history", return_value=[],
        ) as m:
            _run(INTENT_GET_ORDER_HISTORY, {"limit": "abc"})
        _, kwargs = m.call_args
        assert kwargs["limit"] == 5


class TestSelectListedPromoSearchFallback:
    """Regression: when SELECT_LISTED_PROMO can't resolve a query, the
    flow should hand off to order/SEARCH_PRODUCTS instead of replying
    "no tengo una promo activa con ese nombre" — UNLESS the query has
    an explicit promo keyword.

    Production 2026-05-06 (Biela / 3177000722): "buenas tiene la del
    concurso?" → SELECT_LISTED_PROMO with query="la del concurso" →
    cold-ask matched 0 promos → reply "no hay promo con ese nombre",
    when the user clearly meant a product. The handoff sends the
    descriptive query to the order agent's catalog search (which has
    fuzzy + semantic + tag matching).
    """

    def _run_select(self, query, listed=None):
        session = {
            "agent_contexts": {
                "customer_service": {"last_listed_promos": listed or []},
            },
        }
        return csf._handle_select_listed_promo(
            "+573001234567", "biz-1", {"query": query}, session,
        )

    def test_unresolved_query_hands_off_to_order_when_no_promo_keyword(self):
        # No promo listing, no promo keyword in query → handoff.
        with patch.object(csf.promotion_service, "find_promo_by_query", return_value=[]):
            result = self._run_select("la del concurso")
        assert result["result_kind"] == csf.RESULT_KIND_HANDOFF
        assert result["handoff"]["to"] == "order"
        # Order agent receives the user's exact descriptive phrase as
        # the segment so SEARCH_PRODUCTS gets the full query.
        assert result["handoff"]["segment"] == "la del concurso"
        assert result["handoff"]["context"]["reason"] == "promo_query_no_match_search_fallback"

    def test_unresolved_query_with_promo_keyword_returns_not_resolved(self):
        # "promo del concurso" — explicit promo keyword. The user meant
        # a promo; don't sneakily redirect to product search.
        with patch.object(csf.promotion_service, "find_promo_by_query", return_value=[]):
            result = self._run_select("promo del concurso")
        assert result["result_kind"] == csf.RESULT_KIND_PROMO_NOT_RESOLVED

    def test_unresolved_query_with_oferta_keyword_returns_not_resolved(self):
        with patch.object(csf.promotion_service, "find_promo_by_query", return_value=[]):
            result = self._run_select("oferta del lunes")
        assert result["result_kind"] == csf.RESULT_KIND_PROMO_NOT_RESOLVED

    def test_unresolved_with_recent_promo_listing_returns_not_resolved(self):
        # When the bot just listed promos and the user's pick doesn't
        # match any listed name, stay in the "not resolved" path —
        # asking the customer to pick from the listed set is right.
        listed = [{"id": "p1", "name": "Honey Burger Combo"}]
        with patch.object(csf.promotion_service, "find_promo_by_query", return_value=[]):
            result = self._run_select("la que no existe", listed=listed)
        assert result["result_kind"] == csf.RESULT_KIND_PROMO_NOT_RESOLVED

    def test_resolved_with_unique_match_still_hands_off_with_promo_id(self):
        # Sanity check: a unique cold-ask match doesn't accidentally
        # take the search-fallback branch — it stays as a promo handoff
        # with the resolved promo_id.
        with patch.object(
            csf.promotion_service, "find_promo_by_query",
            return_value=[{"id": "p1", "name": "Honey Burger Combo"}],
        ):
            result = self._run_select("honey burger")
        assert result["result_kind"] == csf.RESULT_KIND_HANDOFF
        assert result["handoff"]["context"].get("promo_id") == "p1"

    def test_empty_query_with_no_listing_returns_not_resolved(self):
        # No query, no listed set, nothing to resolve — stay in NOT_RESOLVED
        # rather than handing off an empty segment to the order agent.
        result = self._run_select("")
        assert result["result_kind"] == csf.RESULT_KIND_PROMO_NOT_RESOLVED


# TestChatFallback removed: the chat-fallback branch lived in the old
# execute_customer_service_intent dispatcher. The LLM now handles chat
# turns directly by emitting prose without a tool call — there's no
# handler-level fallback to test.
