"""
Unit tests for app/services/promotion_service.py.

Focused on the matching logic (find_promo_by_query); the rest of the
module is exercised end-to-end via the order-tool tests.
"""

from unittest.mock import patch

import pytest

from app.services import promotion_service


BIZ = "biz-1"


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
