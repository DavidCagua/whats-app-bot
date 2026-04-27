"""Unit tests for app/services/business_info_service.py."""

import pytest

from app.services import business_info_service as bis


def _ctx(settings: dict) -> dict:
    return {"business": {"name": "Biela", "settings": settings}}


class TestGetBusinessInfo:
    def test_hours_from_hours_text(self):
        result = bis.get_business_info(_ctx({"hours_text": "Lun-Vie 5PM a 10PM"}), "hours")
        assert result == "Lun-Vie 5PM a 10PM"

    def test_hours_legacy_key_hours(self):
        result = bis.get_business_info(_ctx({"hours": "10am-10pm todos los días"}), "hours")
        assert result == "10am-10pm todos los días"

    def test_hours_text_takes_precedence_over_legacy(self):
        result = bis.get_business_info(
            _ctx({"hours_text": "Nueva", "hours": "Vieja"}), "hours",
        )
        assert result == "Nueva"

    def test_address(self):
        result = bis.get_business_info(_ctx({"address": "Cra 7 #45-23"}), "address")
        assert result == "Cra 7 #45-23"

    def test_phone(self):
        result = bis.get_business_info(_ctx({"phone": "+573001234567"}), "phone")
        assert result == "+573001234567"

    def test_phone_fallback_to_contact_phone(self):
        result = bis.get_business_info(_ctx({"contact_phone": "3001234567"}), "phone")
        assert result == "3001234567"

    def test_delivery_fee_formats_cop(self):
        result = bis.get_business_info(_ctx({"delivery_fee": 5000}), "delivery_fee")
        assert result == "$5.000"

    def test_delivery_fee_zero_is_valid_free_delivery(self):
        result = bis.get_business_info(_ctx({"delivery_fee": 0}), "delivery_fee")
        assert result == "$0"

    def test_delivery_fee_falls_back_to_default_when_unset(self):
        """
        Regression: CS used to return None ("no configurado") when
        settings.delivery_fee was absent, while the order side silently
        applied the hardcoded $5.000 default. The two surfaces disagreed
        about the same number. Now the lookup uses DELIVERY_FEE_DEFAULT
        as a fallback so receipts and CS info answers stay in sync.
        """
        result = bis.get_business_info(_ctx({}), "delivery_fee")
        assert result == "$5.000"

    def test_delivery_fee_falls_back_when_explicitly_none(self):
        """None / "" are treated as absent — same fallback applies."""
        assert bis.get_business_info(_ctx({"delivery_fee": None}), "delivery_fee") == "$5.000"
        assert bis.get_business_info(_ctx({"delivery_fee": ""}), "delivery_fee") == "$5.000"

    def test_delivery_fee_default_matches_order_side(self):
        """The constant CS uses must equal the constant the order side
        falls back to, otherwise receipts would charge X but CS would
        say Y. Pin the value so a future refactor that splits them
        breaks the test loudly."""
        from app.services.order_tools import _get_delivery_fee as order_fee
        from app.orchestration.order_flow import _get_delivery_fee as flow_fee
        # No business_context → both fall back to the same default.
        assert order_fee(None) == float(bis.DELIVERY_FEE_DEFAULT)
        assert flow_fee(None) == float(bis.DELIVERY_FEE_DEFAULT)
        # Empty settings → same default again.
        empty_ctx = {"business": {"settings": {}}}
        assert order_fee(empty_ctx) == float(bis.DELIVERY_FEE_DEFAULT)
        assert flow_fee(empty_ctx) == float(bis.DELIVERY_FEE_DEFAULT)

    def test_menu_url(self):
        result = bis.get_business_info(_ctx({"menu_url": "https://x.test"}), "menu_url")
        assert result == "https://x.test"

    def test_payment_methods_list_of_three(self):
        result = bis.get_business_info(
            _ctx({"payment_methods": ["efectivo", "nequi", "tarjeta"]}), "payment_methods",
        )
        assert result == "efectivo, nequi y tarjeta"

    def test_payment_methods_list_of_two(self):
        result = bis.get_business_info(
            _ctx({"payment_methods": ["nequi", "efectivo"]}), "payment_methods",
        )
        assert result == "nequi y efectivo"

    def test_payment_methods_single(self):
        result = bis.get_business_info(
            _ctx({"payment_methods": ["nequi"]}), "payment_methods",
        )
        assert result == "nequi"

    def test_payment_methods_string_pass_through(self):
        result = bis.get_business_info(
            _ctx({"payment_methods": "Nequi o efectivo"}), "payment_methods",
        )
        assert result == "Nequi o efectivo"

    def test_unknown_field_returns_none(self):
        result = bis.get_business_info(_ctx({"hours_text": "x"}), "floor_plan")
        assert result is None

    def test_missing_field_returns_none(self):
        result = bis.get_business_info(_ctx({}), "hours")
        assert result is None

    def test_null_context_returns_none(self):
        assert bis.get_business_info(None, "hours") is None

    def test_supported_fields_matches_constants(self):
        assert set(bis.supported_fields()) == {
            bis.FIELD_HOURS, bis.FIELD_ADDRESS, bis.FIELD_PHONE,
            bis.FIELD_DELIVERY_FEE, bis.FIELD_MENU_URL, bis.FIELD_PAYMENT_METHODS,
        }
