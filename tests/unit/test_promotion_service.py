"""
Unit tests for app/services/promotion_service.py.

Covers find_promo_by_query (matching logic) and list_promos_for_listing
(active-now vs upcoming bucketing). The rest of the module is exercised
end-to-end via the order-tool tests.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import promotion_service


BIZ = "biz-1"
BIZ_UUID = "00000000-0000-0000-0000-000000000001"


def _patch_active(promos):
    return patch(
        "app.services.promotion_service.list_active_promos",
        return_value=list(promos),
    )


class TestFindPromoByQuery:
    """
    Regression: production 2026-05-11 (Biela / 3177000722) — customer
    asked for "Dos Misuri con papas" (Wednesday-only). The Wednesday
    promo was schedule-filtered out, leaving only "Dos Oregon con papas"
    active on Monday. The old pass3 ("any token") matched on "dos" /
    "papas" and silently substituted Oregon for Misuri, adding the
    wrong promo to the cart.

    Pass3 is now restricted to single-token queries.
    """

    def test_multi_token_cross_promo_query_does_not_substitute(self):
        active = [{"id": "oregon", "name": "Dos Oregon con papas"}]
        with _patch_active(active):
            result = promotion_service.find_promo_by_query(BIZ, "Dos Misuri con papas")
        assert result == []  # no silent substitution

    def test_single_token_query_still_matches(self):
        # "honey" → single content token → pass3 fires and matches by
        # substring in the promo name. Regression coverage for the
        # benign use case the guard preserves.
        active = [{"id": "h", "name": "Honey Burger Combo"}]
        with _patch_active(active):
            result = promotion_service.find_promo_by_query(BIZ, "honey")
        assert [p["name"] for p in result] == ["Honey Burger Combo"]

    def test_single_token_with_stopwords_around_still_matches(self):
        # "promo del lunes" — "promo" and "del" are stopwords, leaving
        # just ["lunes"]. Single-content-token query, pass3 fires.
        active = [{"id": "m", "name": "Combo Lunes 2x1"}]
        with _patch_active(active):
            result = promotion_service.find_promo_by_query(BIZ, "promo del lunes")
        assert [p["name"] for p in result] == ["Combo Lunes 2x1"]

    def test_full_phrase_substring_still_wins(self):
        # pass1 (full normalized phrase substring) still beats both
        # later passes — this guard only changes the most-lenient one.
        active = [
            {"id": "oregon", "name": "Dos Oregon con papas"},
            {"id": "honey", "name": "Honey Burger Combo"},
        ]
        with _patch_active(active):
            result = promotion_service.find_promo_by_query(BIZ, "Dos Oregon con papas")
        assert [p["name"] for p in result] == ["Dos Oregon con papas"]

    def test_all_tokens_match_still_wins(self):
        # pass2 (all content tokens present) — query "oregon papas"
        # → tokens ["oregon", "papas"], both in "Dos Oregon con papas".
        # The new guard on pass3 doesn't affect this path.
        active = [
            {"id": "oregon", "name": "Dos Oregon con papas"},
            {"id": "honey", "name": "Honey Burger Combo"},
        ]
        with _patch_active(active):
            result = promotion_service.find_promo_by_query(BIZ, "oregon papas")
        assert [p["name"] for p in result] == ["Dos Oregon con papas"]

    def test_empty_query_returns_empty(self):
        active = [{"id": "x", "name": "X"}]
        with _patch_active(active):
            assert promotion_service.find_promo_by_query(BIZ, "") == []
            assert promotion_service.find_promo_by_query(BIZ, "   ") == []

    def test_no_active_promos_returns_empty(self):
        with _patch_active([]):
            assert promotion_service.find_promo_by_query(BIZ, "honey") == []


def _fake_promo(name, *, promo_id="p1", days_of_week=None,
                start_time=None, end_time=None,
                starts_on=None, ends_on=None):
    """Promotion row stand-in carrying just the surface that
    list_promos_for_listing reads (attrs + to_dict)."""
    promo = SimpleNamespace(
        id=promo_id, name=name, days_of_week=days_of_week,
        start_time=start_time, end_time=end_time,
        starts_on=starts_on, ends_on=ends_on,
    )
    promo.to_dict = lambda: {"id": promo_id, "name": name, "days_of_week": days_of_week}
    return promo


def _patch_query(rows):
    session = MagicMock()
    session.query.return_value.options.return_value.filter.return_value.all.return_value = list(rows)
    return patch("app.services.promotion_service.get_db_session", return_value=session)


# Monday 2026-05-11 at 12:00 UTC = Monday 07:00 in Bogota (UTC-5).
# Anchors every "today" calculation in TestListPromosForListing —
# matches the production trace where the Misuri/Oregon substitution
# bug surfaced (Monday, Wednesday-only Misuri filtered out, Oregon
# active).
_MONDAY_UTC = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


class TestListPromosForListing:
    """
    Two-bucket view consumed by the CS get_promos tool. The bucketing is
    schedule math + DB plumbing, not LLM behavior — exercise it
    deterministically so misclassification (active vs upcoming) is caught
    at unit level, not in prod.
    """

    def test_no_business_id_returns_empty_buckets(self):
        assert promotion_service.list_promos_for_listing("") == {
            "active_now": [], "upcoming": [],
        }

    def test_always_on_promo_is_active(self):
        # No day_of_week / time constraints → schedule matches at any
        # `when`, lands in active_now. The `_next_active_iso_weekday`
        # helper short-circuits to None for always-on promos so they
        # never leak into upcoming.
        promo = _fake_promo("Always On", days_of_week=None)
        with _patch_query([promo]):
            result = promotion_service.list_promos_for_listing(
                BIZ_UUID, when=_MONDAY_UTC, timezone_name="America/Bogota",
            )
        assert [p["name"] for p in result["active_now"]] == ["Always On"]
        assert result["upcoming"] == []

    def test_monday_only_promo_on_monday_is_active(self):
        promo = _fake_promo("Oregon", days_of_week=[1])
        with _patch_query([promo]):
            result = promotion_service.list_promos_for_listing(
                BIZ_UUID, when=_MONDAY_UTC, timezone_name="America/Bogota",
            )
        assert [p["name"] for p in result["active_now"]] == ["Oregon"]
        assert result["upcoming"] == []

    def test_wednesday_only_promo_on_monday_is_upcoming_with_day(self):
        # The production scenario behind get_promos surfacing
        # "Disponible el miércoles": Wednesday-only Misuri promo,
        # message arrives Monday. Must show in upcoming with
        # next_active_day=3 so the CS prose can render the day name.
        promo = _fake_promo("Misuri", days_of_week=[3])
        with _patch_query([promo]):
            result = promotion_service.list_promos_for_listing(
                BIZ_UUID, when=_MONDAY_UTC, timezone_name="America/Bogota",
            )
        assert result["active_now"] == []
        assert [p["name"] for p in result["upcoming"]] == ["Misuri"]
        assert result["upcoming"][0]["next_active_day"] == 3

    def test_mixed_active_and_upcoming(self):
        # Mixed bag — Monday-active Oregon stays in active_now,
        # Wednesday-only Misuri goes to upcoming.
        monday = _fake_promo("Oregon", promo_id="p-or", days_of_week=[1])
        wednesday = _fake_promo("Misuri", promo_id="p-mi", days_of_week=[3])
        with _patch_query([monday, wednesday]):
            result = promotion_service.list_promos_for_listing(
                BIZ_UUID, when=_MONDAY_UTC, timezone_name="America/Bogota",
            )
        assert [p["name"] for p in result["active_now"]] == ["Oregon"]
        assert [p["name"] for p in result["upcoming"]] == ["Misuri"]
        assert result["upcoming"][0]["next_active_day"] == 3

    def test_db_exception_returns_empty_buckets(self):
        # If the query layer blows up we must not propagate — the CS
        # path falls back to "no hay promos" rather than 500-ing the
        # turn.
        session = MagicMock()
        session.query.side_effect = RuntimeError("DB exploded")
        with patch("app.services.promotion_service.get_db_session", return_value=session):
            result = promotion_service.list_promos_for_listing(
                BIZ_UUID, when=_MONDAY_UTC, timezone_name="America/Bogota",
            )
        assert result == {"active_now": [], "upcoming": []}
