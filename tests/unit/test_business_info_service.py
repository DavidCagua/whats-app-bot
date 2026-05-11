"""Unit tests for app/services/business_info_service.py."""

from datetime import time as _time
from unittest.mock import patch

import pytest

from app.services import business_info_service as bis


def _ctx(settings: dict, business_id: str = "") -> dict:
    out = {"business": {"name": "Biela", "settings": settings}}
    if business_id:
        out["business_id"] = business_id
    return out


@pytest.fixture(autouse=True)
def _clear_hours_cache():
    bis.invalidate_hours_cache()
    yield
    bis.invalidate_hours_cache()


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
        applied the hardcoded default. The two surfaces disagreed
        about the same number. Now the lookup uses DELIVERY_FEE_DEFAULT
        as a fallback so receipts and CS info answers stay in sync.
        """
        result = bis.get_business_info(_ctx({}), "delivery_fee")
        assert result == "$7.000"

    def test_delivery_fee_falls_back_when_explicitly_none(self):
        """None / "" are treated as absent — same fallback applies."""
        assert bis.get_business_info(_ctx({"delivery_fee": None}), "delivery_fee") == "$7.000"
        assert bis.get_business_info(_ctx({"delivery_fee": ""}), "delivery_fee") == "$7.000"

    def test_delivery_time_reads_from_settings_when_set(self):
        result = bis.get_business_info(
            _ctx({"delivery_time_text": "30 a 45 minutos"}), "delivery_time",
        )
        assert result == "30 a 45 minutos"

    def test_delivery_time_falls_back_to_nominal_range_when_unset(self):
        """
        Regression: customer asks "cuánto se demora la entrega?", CS used
        to fall through to chat-fallback ("no entendí"). Now this maps to
        the delivery_time field, which falls back to the same NOMINAL_RANGE_TEXT
        the order agent quotes at order placement — so the answer matches
        what receipts promise.
        """
        from app.services.order_eta import NOMINAL_RANGE_TEXT
        result = bis.get_business_info(_ctx({}), "delivery_time")
        assert result == NOMINAL_RANGE_TEXT

    def test_delivery_fee_default_matches_order_side(self):
        """The constant CS uses must equal the constant the order side
        falls back to, otherwise receipts would charge X but CS would
        say Y. (The legacy ``order_flow._get_delivery_fee`` mirror was
        removed when v1 was deleted — only ``order_tools._get_delivery_fee``
        remains as the order-side source.)"""
        from app.services.order_tools import _get_delivery_fee as order_fee
        # No business_context → fall back to the documented default.
        assert order_fee(None) == float(bis.DELIVERY_FEE_DEFAULT)
        # Empty settings → same default again.
        empty_ctx = {"business": {"settings": {}}}
        assert order_fee(empty_ctx) == float(bis.DELIVERY_FEE_DEFAULT)

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

    def test_payment_details_returns_configured_value(self):
        result = bis.get_business_info(
            _ctx({"payment_details": "El pago es directo con el domiciliario, contra entrega."}),
            "payment_details",
        )
        assert result == "El pago es directo con el domiciliario, contra entrega."

    def test_payment_details_falls_back_to_contra_entrega_default(self):
        # Operators don't always configure this. The safe default is the
        # contra-entrega text — never the business contact phone, which
        # is what the CS agent used to return when misclassifying these
        # questions as `phone` (Biela 2026-05-06 incident).
        assert bis.get_business_info(_ctx({}), "payment_details") == (
            "El pago es contra entrega, directo con el domiciliario."
        )

    def test_payment_details_falls_back_when_explicitly_none(self):
        assert bis.get_business_info(_ctx({"payment_details": None}), "payment_details") == (
            "El pago es contra entrega, directo con el domiciliario."
        )
        assert bis.get_business_info(_ctx({"payment_details": ""}), "payment_details") == (
            "El pago es contra entrega, directo con el domiciliario."
        )

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
            bis.FIELD_DELIVERY_FEE, bis.FIELD_DELIVERY_TIME,
            bis.FIELD_MENU_URL, bis.FIELD_PAYMENT_METHODS,
            bis.FIELD_PAYMENT_DETAILS,
        }


class TestHoursFromBusinessAvailability:
    """
    Hours are now sourced from the structured ``business_availability``
    table; ``business.settings.hours_text`` is the fallback for
    businesses that haven't migrated.
    """

    def _row(self, dow, open_h, open_m, close_h, close_m, active=True):
        return {
            "day_of_week": dow,
            "open_time": _time(open_h, open_m),
            "close_time": _time(close_h, close_m),
            "is_active": active,
        }

    def test_hours_renders_mon_to_fri_range_with_distinct_saturday(self):
        rows = [self._row(d, 17, 30, 22, 0) for d in (1, 2, 3, 4, 5)]
        rows.append(self._row(6, 18, 0, 23, 0))
        with patch(
            "app.services.business_info_service._load_hours_from_availability",
            return_value=bis._condense_hours_rows(rows),
        ):
            result = bis.get_business_info(
                _ctx({}, business_id="biz-1"), "hours",
            )
        assert "Lun a Vie" in result
        assert "5:30 PM" in result
        assert "10:00 PM" in result
        assert "Sáb" in result
        assert "11:00 PM" in result

    def test_hours_falls_back_to_settings_when_no_availability_rows(self):
        # Empty availability → fall back to the legacy hours_text.
        with patch(
            "app.services.business_info_service._load_hours_from_availability",
            return_value=None,
        ):
            result = bis.get_business_info(
                _ctx({"hours_text": "Lun-Vie 5PM a 10PM"}, business_id="biz-1"),
                "hours",
            )
        assert result == "Lun-Vie 5PM a 10PM"

    def test_hours_settings_override_used_when_business_id_missing(self):
        # No business_id in context → cannot consult availability;
        # legacy settings.hours_text path runs.
        result = bis.get_business_info(
            _ctx({"hours_text": "10am-10pm"}), "hours",
        )
        assert result == "10am-10pm"

    def test_availability_takes_precedence_over_settings(self):
        with patch(
            "app.services.business_info_service._load_hours_from_availability",
            return_value="Lun a Vie: 5:30 PM - 10:00 PM",
        ):
            result = bis.get_business_info(
                _ctx({"hours_text": "STALE TEXT"}, business_id="biz-1"),
                "hours",
            )
        assert "STALE" not in result
        assert "Lun a Vie" in result


class TestCondenseHoursRows:
    """Direct unit tests for the row-formatter."""

    def test_groups_consecutive_same_window_days(self):
        rows = [
            {"day_of_week": 1, "open_time": _time(10, 0), "close_time": _time(20, 0), "is_active": True},
            {"day_of_week": 2, "open_time": _time(10, 0), "close_time": _time(20, 0), "is_active": True},
            {"day_of_week": 3, "open_time": _time(10, 0), "close_time": _time(20, 0), "is_active": True},
        ]
        out = bis._condense_hours_rows(rows)
        assert out == "Lun a Mié: 10:00 AM - 8:00 PM"

    def test_skips_inactive_rows(self):
        rows = [
            {"day_of_week": 1, "open_time": _time(10, 0), "close_time": _time(20, 0), "is_active": False},
            {"day_of_week": 2, "open_time": _time(10, 0), "close_time": _time(20, 0), "is_active": True},
        ]
        out = bis._condense_hours_rows(rows)
        assert out == "Mar: 10:00 AM - 8:00 PM"

    def test_empty_returns_empty_string(self):
        assert bis._condense_hours_rows([]) == ""

    def test_widest_window_per_day_when_multiple_rows(self):
        # Two staff rows for Monday with different windows → widest wins.
        rows = [
            {"day_of_week": 1, "open_time": _time(10, 0), "close_time": _time(18, 0), "is_active": True},
            {"day_of_week": 1, "open_time": _time(8, 0), "close_time": _time(20, 0), "is_active": True},
        ]
        out = bis._condense_hours_rows(rows)
        assert "8:00 AM" in out
        assert "8:00 PM" in out

    def test_sunday_renders_separately_when_distinct(self):
        rows = [
            {"day_of_week": 1, "open_time": _time(17, 30), "close_time": _time(22, 0), "is_active": True},
            {"day_of_week": 0, "open_time": _time(11, 0), "close_time": _time(15, 0), "is_active": True},
        ]
        out = bis._condense_hours_rows(rows)
        assert "Lun" in out
        assert "Dom" in out


class TestComputeOpenStatus:
    """
    Live "are we open right now?" check. The bug we're guarding against
    (Biela / 3177000722, 2026-05-05): bot answered "Sí, estamos abiertos"
    at 00:42 Bogotá when the store opens at 17:00.
    """

    # Biela's actual schedule (from production):
    #   Sun closed (is_active=False)
    #   Mon-Thu 17:00-22:00
    #   Fri-Sat 17:00-22:30
    BIELA_ROWS = [
        {"day_of_week": 0, "open_time": _time(17, 30), "close_time": _time(22, 0), "is_active": False},
        {"day_of_week": 1, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 2, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 3, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 4, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 5, "open_time": _time(17, 0), "close_time": _time(22, 30), "is_active": True},
        {"day_of_week": 6, "open_time": _time(17, 0), "close_time": _time(22, 30), "is_active": True},
    ]

    def _run(self, iso, rows=None):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            pytest.skip("zoneinfo unavailable")
        rows = rows if rows is not None else self.BIELA_ROWS
        now = datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo("America/Bogota"))
        with patch(
            "app.services.business_info_service._load_active_availability_rows",
            return_value=rows,
        ):
            return bis.compute_open_status("biz", now=now)

    def test_open_during_window(self):
        # Tuesday 18:00 — within Tue 17:00-22:00.
        s = self._run("2026-05-05T18:00:00")
        assert s["has_data"] is True
        assert s["is_open"] is True
        assert s["closes_at"] == _time(22, 0)

    def test_closed_before_open_today(self):
        # Tuesday 14:45 — before today's 17:00 open.
        s = self._run("2026-05-05T14:45:00")
        assert s["is_open"] is False
        assert s["opens_at"] == _time(17, 0)
        assert s["next_open_dow"] == 2  # Tuesday
        assert s["next_open_time"] == _time(17, 0)

    def test_closed_after_close_today(self):
        # Tuesday 22:30 — after Tue's 22:00 close. Next open: Wed 17:00.
        s = self._run("2026-05-05T22:30:00")
        assert s["is_open"] is False
        # next_open should be Wednesday (3), not Tuesday.
        assert s["next_open_dow"] == 3
        assert s["next_open_time"] == _time(17, 0)

    def test_post_midnight_same_day_in_db_terms(self):
        # Wednesday 00:42 Bogotá — the original production bug timestamp.
        # Should be CLOSED (Tue 22:00 close already passed in calendar).
        # Next open is today at 17:00 (Wed).
        s = self._run("2026-05-06T00:42:00")
        assert s["is_open"] is False
        assert s["opens_at"] == _time(17, 0)

    def test_sunday_inactive_skipped(self):
        # Sunday 19:00 — Sun row is inactive. next_open should be Monday.
        s = self._run("2026-05-10T19:00:00")
        assert s["is_open"] is False
        assert s["next_open_dow"] == 1  # Monday
        assert s["next_open_time"] == _time(17, 0)

    def test_after_saturday_close_skips_sunday(self):
        # Saturday 23:00 — after Sat's 22:30 close. Next open: Monday
        # (Sunday is inactive so it's skipped).
        s = self._run("2026-05-09T23:00:00")
        assert s["is_open"] is False
        assert s["next_open_dow"] == 1

    def test_no_rows_returns_no_data(self):
        s = self._run("2026-05-05T18:00:00", rows=[])
        assert s["has_data"] is False
        assert s["is_open"] is False

    def test_empty_business_id_returns_no_data(self):
        with patch(
            "app.services.business_info_service._load_active_availability_rows",
            return_value=[],
        ) as loader:
            s = bis.compute_open_status("")
        loader.assert_not_called()
        assert s["has_data"] is False


class TestIsTakingOrdersNow:
    """
    Order-availability gate. Returns can_take_orders=True/False with
    reason in {"open", "closed", "no_data"}. Default-open when the
    business has no availability rows configured (gate is opt-in via
    the presence of business_availability data).
    """

    BIELA_ROWS = [
        {"day_of_week": 0, "open_time": _time(17, 30), "close_time": _time(22, 0), "is_active": False},
        {"day_of_week": 1, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 2, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 3, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 4, "open_time": _time(17, 0), "close_time": _time(22, 0), "is_active": True},
        {"day_of_week": 5, "open_time": _time(17, 0), "close_time": _time(22, 30), "is_active": True},
        {"day_of_week": 6, "open_time": _time(17, 0), "close_time": _time(22, 30), "is_active": True},
    ]

    def _run(self, iso, rows=None):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            pytest.skip("zoneinfo unavailable")
        rows = rows if rows is not None else self.BIELA_ROWS
        now = datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo("America/Bogota"))
        with patch(
            "app.services.business_info_service._load_active_availability_rows",
            return_value=rows,
        ):
            return bis.is_taking_orders_now("biz", now=now)

    def test_open_returns_can_take_orders(self):
        gate = self._run("2026-05-05T18:00:00")
        assert gate["can_take_orders"] is True
        assert gate["reason"] == "open"

    def test_closed_returns_cannot_take_orders(self):
        gate = self._run("2026-05-05T14:45:00")
        assert gate["can_take_orders"] is False
        assert gate["reason"] == "closed"
        assert gate["opens_at"] == _time(17, 0)
        assert gate["next_open_dow"] == 2  # Tuesday
        assert gate["next_open_time"] == _time(17, 0)

    def test_after_close_returns_cannot_take_orders(self):
        # Tue 22:30 → past close, next open is Wednesday.
        gate = self._run("2026-05-05T22:30:00")
        assert gate["can_take_orders"] is False
        assert gate["reason"] == "closed"
        assert gate["next_open_dow"] == 3

    def test_no_availability_rows_defaults_to_open(self):
        """Gate is opt-in via the presence of availability data —
        a business without configured rows keeps accepting orders."""
        gate = self._run("2026-05-05T18:00:00", rows=[])
        assert gate["can_take_orders"] is True
        assert gate["reason"] == "no_data"

    def test_exact_close_time_is_closed(self):
        """Boundary: at exactly close_time we treat as closed
        (compute_open_status uses ot <= cur_time < ct)."""
        gate = self._run("2026-05-05T22:00:00")
        assert gate["can_take_orders"] is False
        assert gate["reason"] == "closed"

    def test_exact_open_time_is_open(self):
        """Boundary: at exactly open_time we treat as open."""
        gate = self._run("2026-05-05T17:00:00")
        assert gate["can_take_orders"] is True
        assert gate["reason"] == "open"


class TestFormatOpenStatusSentence:
    """Spanish copy for the open-status sentence."""

    def _status(self, **overrides):
        base = {
            "is_open": False,
            "has_data": True,
            "opens_at": None,
            "closes_at": None,
            "next_open_dow": None,
            "next_open_time": None,
            "now_local": None,
        }
        base.update(overrides)
        return base

    def test_open_with_close_time(self):
        s = self._status(is_open=True, closes_at=_time(22, 0))
        out = bis.format_open_status_sentence(s)
        assert "Sí, estamos abiertos" in out
        assert "10:00 PM" in out

    def test_closed_opens_today(self):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            pytest.skip("zoneinfo unavailable")
        # Tuesday 14:45 — closed, opens today at 17:00.
        now = datetime.fromisoformat("2026-05-05T14:45:00").replace(
            tzinfo=ZoneInfo("America/Bogota"))
        s = self._status(
            now_local=now, next_open_dow=2, next_open_time=_time(17, 0),
        )
        out = bis.format_open_status_sentence(s)
        assert "Por ahora estamos cerrados" in out
        assert "Hoy abrimos" in out
        assert "5:00 PM" in out

    def test_closed_opens_other_day(self):
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            pytest.skip("zoneinfo unavailable")
        # Sunday — closed, next open Monday at 17:00.
        now = datetime.fromisoformat("2026-05-10T19:00:00").replace(
            tzinfo=ZoneInfo("America/Bogota"))
        s = self._status(
            now_local=now, next_open_dow=1, next_open_time=_time(17, 0),
        )
        out = bis.format_open_status_sentence(s)
        assert "Volvemos a abrir el lunes" in out
        assert "5:00 PM" in out

    def test_no_data_returns_empty(self):
        s = self._status(has_data=False)
        out = bis.format_open_status_sentence(s)
        assert out == ""
