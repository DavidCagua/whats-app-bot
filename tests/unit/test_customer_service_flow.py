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
