"""Unit tests for app/services/payment_config.py."""

import pytest

from app.services import payment_config as pc


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def biela_settings():
    """Realistic settings shape that mirrors what Phase-1-migrated Biela looks like."""
    return {
        "payment_methods": [
            {
                "name": "Efectivo",
                "contexts": [pc.CONTEXT_DELIVERY_ON_FULFILLMENT, pc.CONTEXT_ON_SITE_ON_FULFILLMENT],
            },
            {
                "name": "Tarjeta",
                "contexts": [pc.CONTEXT_ON_SITE_ON_FULFILLMENT],
            },
            {
                "name": "Nequi",
                "contexts": [
                    pc.CONTEXT_DELIVERY_PAY_NOW,
                    pc.CONTEXT_DELIVERY_ON_FULFILLMENT,
                    pc.CONTEXT_ON_SITE_PAY_NOW,
                    pc.CONTEXT_ON_SITE_ON_FULFILLMENT,
                ],
            },
            {
                "name": "Transferencia",
                "contexts": [pc.CONTEXT_DELIVERY_PAY_NOW, pc.CONTEXT_ON_SITE_PAY_NOW],
            },
            {
                "name": "Llave BreB",
                "contexts": [pc.CONTEXT_DELIVERY_PAY_NOW, pc.CONTEXT_ON_SITE_PAY_NOW],
            },
        ],
        "payment_destinations": {
            "Nequi": "300 123 4567 (Biela SAS)",
            "Transferencia": "Bancolombia 123-456789-00",
            "Llave BreB": "@biela",
        },
    }


# ── contexts_for_fulfillment ──────────────────────────────────────────────────


class TestContextsForFulfillment:
    def test_delivery(self):
        assert pc.contexts_for_fulfillment("delivery") == [
            pc.CONTEXT_DELIVERY_PAY_NOW,
            pc.CONTEXT_DELIVERY_ON_FULFILLMENT,
        ]

    @pytest.mark.parametrize("ft", ["pickup", "dine_in", "on_site"])
    def test_on_site_variants(self, ft):
        assert pc.contexts_for_fulfillment(ft) == [
            pc.CONTEXT_ON_SITE_PAY_NOW,
            pc.CONTEXT_ON_SITE_ON_FULFILLMENT,
        ]

    @pytest.mark.parametrize("ft", [None, "", "unknown", "wat"])
    def test_unknown_returns_all(self, ft):
        assert set(pc.contexts_for_fulfillment(ft)) == set(pc.ALL_CONTEXTS)


# ── get_payment_methods (normalization) ───────────────────────────────────────


class TestGetPaymentMethods:
    def test_returns_normalized_list(self, biela_settings):
        methods = pc.get_payment_methods(biela_settings)
        names = [m["name"] for m in methods]
        assert names == ["Efectivo", "Tarjeta", "Nequi", "Transferencia", "Llave BreB"]

    def test_none_settings(self):
        assert pc.get_payment_methods(None) == []

    def test_missing_key(self):
        assert pc.get_payment_methods({}) == []

    def test_drops_entries_without_name(self):
        settings = {
            "payment_methods": [
                {"name": "", "contexts": [pc.CONTEXT_ON_SITE_ON_FULFILLMENT]},
                {"contexts": [pc.CONTEXT_ON_SITE_ON_FULFILLMENT]},
                {"name": "  ", "contexts": [pc.CONTEXT_ON_SITE_ON_FULFILLMENT]},
                {"name": "Efectivo", "contexts": [pc.CONTEXT_ON_SITE_ON_FULFILLMENT]},
            ]
        }
        assert [m["name"] for m in pc.get_payment_methods(settings)] == ["Efectivo"]

    def test_drops_non_dict_entries(self):
        settings = {"payment_methods": ["Efectivo", None, 42, {"name": "Nequi", "contexts": []}]}
        assert [m["name"] for m in pc.get_payment_methods(settings)] == ["Nequi"]

    def test_contexts_filtered_to_strings(self):
        settings = {
            "payment_methods": [
                {"name": "Efectivo", "contexts": ["delivery_on_fulfillment", None, "", 99]}
            ]
        }
        methods = pc.get_payment_methods(settings)
        assert methods[0]["contexts"] == ["delivery_on_fulfillment", "99"]


# ── get_payment_methods_for ───────────────────────────────────────────────────


class TestGetPaymentMethodsFor:
    def test_delivery_on_fulfillment(self, biela_settings):
        result = pc.get_payment_methods_for(pc.CONTEXT_DELIVERY_ON_FULFILLMENT, biela_settings)
        assert result == ["Efectivo", "Nequi"]

    def test_delivery_pay_now(self, biela_settings):
        result = pc.get_payment_methods_for(pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings)
        assert result == ["Nequi", "Transferencia", "Llave BreB"]

    def test_on_site_on_fulfillment_includes_card(self, biela_settings):
        result = pc.get_payment_methods_for(pc.CONTEXT_ON_SITE_ON_FULFILLMENT, biela_settings)
        assert result == ["Efectivo", "Tarjeta", "Nequi"]

    def test_on_site_pay_now(self, biela_settings):
        result = pc.get_payment_methods_for(pc.CONTEXT_ON_SITE_PAY_NOW, biela_settings)
        assert result == ["Nequi", "Transferencia", "Llave BreB"]

    def test_unknown_context_returns_empty(self, biela_settings):
        assert pc.get_payment_methods_for("nonsense", biela_settings) == []

    def test_empty_settings(self):
        assert pc.get_payment_methods_for(pc.CONTEXT_DELIVERY_PAY_NOW, {}) == []

    def test_preserves_method_order(self):
        settings = {
            "payment_methods": [
                {"name": "Z", "contexts": [pc.CONTEXT_DELIVERY_PAY_NOW]},
                {"name": "A", "contexts": [pc.CONTEXT_DELIVERY_PAY_NOW]},
                {"name": "M", "contexts": [pc.CONTEXT_DELIVERY_PAY_NOW]},
            ]
        }
        assert pc.get_payment_methods_for(pc.CONTEXT_DELIVERY_PAY_NOW, settings) == ["Z", "A", "M"]


# ── get_payment_methods_for_any ───────────────────────────────────────────────


class TestGetPaymentMethodsForAny:
    def test_both_delivery_contexts_dedup(self, biela_settings):
        result = pc.get_payment_methods_for_any(
            [pc.CONTEXT_DELIVERY_PAY_NOW, pc.CONTEXT_DELIVERY_ON_FULFILLMENT],
            biela_settings,
        )
        # Nequi is in both — should appear once.
        assert result == ["Efectivo", "Nequi", "Transferencia", "Llave BreB"]

    def test_all_contexts_returns_every_method(self, biela_settings):
        result = pc.get_payment_methods_for_any(pc.ALL_CONTEXTS, biela_settings)
        assert result == ["Efectivo", "Tarjeta", "Nequi", "Transferencia", "Llave BreB"]

    def test_empty_contexts_returns_empty(self, biela_settings):
        assert pc.get_payment_methods_for_any([], biela_settings) == []


# ── get_payment_destination ───────────────────────────────────────────────────


class TestGetPaymentDestination:
    def test_known_method(self, biela_settings):
        assert pc.get_payment_destination("Nequi", biela_settings) == "300 123 4567 (Biela SAS)"

    def test_case_insensitive(self, biela_settings):
        assert pc.get_payment_destination("nequi", biela_settings) == "300 123 4567 (Biela SAS)"
        assert pc.get_payment_destination("NEQUI", biela_settings) == "300 123 4567 (Biela SAS)"

    def test_trims_whitespace(self, biela_settings):
        assert pc.get_payment_destination("  Nequi  ", biela_settings) == "300 123 4567 (Biela SAS)"

    def test_unknown_method_returns_none(self, biela_settings):
        assert pc.get_payment_destination("Bitcoin", biela_settings) is None

    def test_empty_method_returns_none(self, biela_settings):
        assert pc.get_payment_destination("", biela_settings) is None

    def test_no_destinations_configured(self):
        settings = {"payment_methods": []}
        assert pc.get_payment_destination("Nequi", settings) is None

    def test_empty_destination_value_returns_none(self):
        settings = {"payment_destinations": {"Nequi": ""}}
        assert pc.get_payment_destination("Nequi", settings) is None


# ── is_method_valid_for_context ───────────────────────────────────────────────


class TestIsMethodValidForContext:
    def test_tarjeta_invalid_for_delivery(self, biela_settings):
        assert not pc.is_method_valid_for_context(
            "Tarjeta", pc.CONTEXT_DELIVERY_ON_FULFILLMENT, biela_settings
        )
        assert not pc.is_method_valid_for_context(
            "Tarjeta", pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings
        )

    def test_tarjeta_valid_for_on_site_on_fulfillment(self, biela_settings):
        assert pc.is_method_valid_for_context(
            "Tarjeta", pc.CONTEXT_ON_SITE_ON_FULFILLMENT, biela_settings
        )

    def test_efectivo_invalid_for_pay_now(self, biela_settings):
        assert not pc.is_method_valid_for_context(
            "Efectivo", pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings
        )
        assert not pc.is_method_valid_for_context(
            "Efectivo", pc.CONTEXT_ON_SITE_PAY_NOW, biela_settings
        )

    def test_nequi_valid_everywhere(self, biela_settings):
        for ctx in pc.ALL_CONTEXTS:
            assert pc.is_method_valid_for_context("Nequi", ctx, biela_settings), ctx

    def test_case_insensitive(self, biela_settings):
        assert pc.is_method_valid_for_context(
            "TARJETA", pc.CONTEXT_ON_SITE_ON_FULFILLMENT, biela_settings
        )
        assert pc.is_method_valid_for_context(
            "  nequi  ", pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings
        )

    def test_empty_inputs(self, biela_settings):
        assert not pc.is_method_valid_for_context("", pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings)
        assert not pc.is_method_valid_for_context("Nequi", "", biela_settings)

    def test_unknown_method(self, biela_settings):
        assert not pc.is_method_valid_for_context(
            "Bitcoin", pc.CONTEXT_DELIVERY_PAY_NOW, biela_settings
        )


# ── is_method_valid_for_fulfillment ───────────────────────────────────────────


class TestIsMethodValidForFulfillment:
    def test_tarjeta_invalid_for_delivery(self, biela_settings):
        assert not pc.is_method_valid_for_fulfillment("Tarjeta", "delivery", biela_settings)

    @pytest.mark.parametrize("ft", ["pickup", "dine_in", "on_site"])
    def test_tarjeta_valid_for_on_site(self, ft, biela_settings):
        assert pc.is_method_valid_for_fulfillment("Tarjeta", ft, biela_settings)

    def test_nequi_valid_for_delivery(self, biela_settings):
        # Nequi works delivery_on_fulfillment so should pass.
        assert pc.is_method_valid_for_fulfillment("Nequi", "delivery", biela_settings)

    def test_efectivo_valid_for_delivery(self, biela_settings):
        assert pc.is_method_valid_for_fulfillment("Efectivo", "delivery", biela_settings)

    def test_transferencia_valid_for_delivery(self, biela_settings):
        # Transferencia only in delivery_pay_now; still passes the
        # "any context for this fulfillment" check.
        assert pc.is_method_valid_for_fulfillment("Transferencia", "delivery", biela_settings)

    def test_unknown_method_invalid(self, biela_settings):
        assert not pc.is_method_valid_for_fulfillment("Bitcoin", "delivery", biela_settings)

    def test_no_fulfillment_is_permissive(self, biela_settings):
        # Pre-decision turns shouldn't trip validation.
        assert pc.is_method_valid_for_fulfillment("Tarjeta", None, biela_settings)
        assert pc.is_method_valid_for_fulfillment("Tarjeta", "", biela_settings)

    def test_empty_method_invalid(self, biela_settings):
        assert not pc.is_method_valid_for_fulfillment("", "delivery", biela_settings)
