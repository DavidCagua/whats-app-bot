"""Unit tests for app/orchestration/customer_service_flow.py."""

from unittest.mock import patch

import pytest

from app.orchestration import customer_service_flow as csf


WA = "+573001234567"
BIZ = "biz-1"
BIZ_CTX = {"business_id": BIZ, "business": {"name": "Biela", "settings": {"hours_text": "Lun-Vie 5PM"}}}


def _run(intent, params=None, ctx=BIZ_CTX):
    return csf.execute_customer_service_intent(
        wa_id=WA, business_id=BIZ, business_context=ctx,
        intent=intent, params=params or {},
    )


class TestGetBusinessInfo:
    def test_found_field_returns_business_info_result(self):
        result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        assert result["result_kind"] == csf.RESULT_KIND_BUSINESS_INFO
        assert result["success"] is True
        assert result["field"] == "hours"
        assert result["value"] == "Lun-Vie 5PM"

    def test_unknown_field_returns_info_missing(self):
        result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "floor_plan"})
        assert result["result_kind"] == csf.RESULT_KIND_INFO_MISSING
        assert result["field"] == "floor_plan"
        assert "hours" in result["available_fields"]

    def test_missing_field_in_params_returns_info_missing(self):
        result = _run(csf.INTENT_GET_BUSINESS_INFO, {})
        assert result["result_kind"] == csf.RESULT_KIND_INFO_MISSING
        assert result["field"] is None

    def test_field_not_configured_returns_info_missing(self):
        ctx = {"business_id": BIZ, "business": {"name": "X", "settings": {}}}
        result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "hours"}, ctx=ctx)
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
            result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "hours"})
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
            result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        value = result["value"]
        assert "Por ahora estamos cerrados" in value
        assert "Hoy abrimos a las 5:00 PM" in value
        assert "Lun a Vie: 5:00 PM - 10:00 PM" in value

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
            result = _run(csf.INTENT_GET_BUSINESS_INFO, {"field": "hours"})
        value = result["value"]
        assert value == "Lun-Vie 5PM"  # the BIZ_CTX settings fallback
        assert "estamos abiertos" not in value
        assert "estamos cerrados" not in value


class TestGetOrderStatus:
    def test_existing_order(self):
        order = {
            "id": "o1", "status": "pending", "total_amount": 25000,
            "delivery_address": "Cra 7", "payment_method": "nequi",
            "notes": None, "created_at": "2026-04-18T12:00:00",
            "items": [{"quantity": 2, "unit_price": 12500}],
        }
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=order):
            result = _run(csf.INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_STATUS
        assert result["order"]["status"] == "pending"
        assert result["order"]["total_amount"] == 25000
        assert result["order"]["items"] == [{"quantity": 2, "unit_price": 12500}]

    def test_no_order_found(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            result = _run(csf.INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_lookup_exception_returns_internal_error(self):
        with patch.object(
            csf.order_lookup_service, "get_latest_order",
            side_effect=RuntimeError("boom"),
        ):
            result = _run(csf.INTENT_GET_ORDER_STATUS)
        assert result["result_kind"] == csf.RESULT_KIND_INTERNAL_ERROR
        assert result["success"] is False


class TestGetOrderStatusActiveCartHandoff:
    """The 'mi pedido' ambiguity: CS should defer to order when cart is active."""

    def _run_with_session(self, session):
        return csf.execute_customer_service_intent(
            wa_id=WA, business_id=BIZ, business_context=BIZ_CTX,
            intent=csf.INTENT_GET_ORDER_STATUS, params={},
            session=session,
        )

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
            result = _run(csf.INTENT_GET_ORDER_HISTORY)
        assert result["result_kind"] == csf.RESULT_KIND_ORDER_HISTORY
        assert len(result["orders"]) == 2
        # Clean shape
        assert set(result["orders"][0].keys()) >= {"id", "status", "total_amount", "items"}

    def test_no_history_returns_no_order(self):
        with patch.object(csf.order_lookup_service, "get_order_history", return_value=[]):
            result = _run(csf.INTENT_GET_ORDER_HISTORY)
        assert result["result_kind"] == csf.RESULT_KIND_NO_ORDER

    def test_limit_clamped_to_20(self):
        with patch.object(
            csf.order_lookup_service, "get_order_history", return_value=[],
        ) as m:
            _run(csf.INTENT_GET_ORDER_HISTORY, {"limit": 999})
        m.assert_called_once()
        _, kwargs = m.call_args
        assert kwargs["limit"] == 20

    def test_invalid_limit_defaults_to_5(self):
        with patch.object(
            csf.order_lookup_service, "get_order_history", return_value=[],
        ) as m:
            _run(csf.INTENT_GET_ORDER_HISTORY, {"limit": "abc"})
        _, kwargs = m.call_args
        assert kwargs["limit"] == 5


class TestChatFallback:
    def test_explicit_chat_returns_fallback(self):
        result = _run(csf.INTENT_CUSTOMER_SERVICE_CHAT)
        assert result["result_kind"] == csf.RESULT_KIND_CHAT_FALLBACK
        assert "hours" in result["available_fields"]

    def test_unknown_intent_falls_back_to_chat(self):
        result = _run("NOT_A_REAL_INTENT")
        assert result["result_kind"] == csf.RESULT_KIND_CHAT_FALLBACK
