"""Unit tests for app/services/business_greeting.py."""

import pytest

from app.services import business_greeting


class TestIsPureGreeting:
    @pytest.mark.parametrize(
        "msg",
        [
            "hola",
            "Hola",
            "HOLA",
            "hola!",
            "hola.",
            "  hola  ",
            "holaaaa",
            "buenas",
            "Buenas",
            "buenos dias",
            "buenos días",
            "buen día",
            "buenas tardes",
            "buenas noches",
            "hey",
            "saludos",
        ],
    )
    def test_pure_greeting_variants_match(self, msg):
        assert business_greeting.is_pure_greeting(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "",
            "   ",
            None,
            "hola quiero una barracuda",
            "hola cómo estás?",
            "buenas, a qué hora abren",
            "quiero pedir",
            "dame una coca",
            "hola cómo están",
            "tienes barracuda",
            "a qué hora abren",
            # Greeting + emoji / extra word should NOT fast-path either.
            "hola 😊",
            "hola amigo",
        ],
    )
    def test_non_pure_greeting_rejected(self, msg):
        assert business_greeting.is_pure_greeting(msg) is False


class TestGetGreeting:
    def test_uses_business_name_from_context(self):
        ctx = {"business": {"name": "Mi Restaurante", "settings": {"menu_url": "https://x.test/menu"}}}
        reply = business_greeting.get_greeting(ctx, "Juan")
        assert "Mi Restaurante" in reply
        assert "https://x.test/menu" in reply

    def test_prepends_real_customer_name(self):
        ctx = {"business": {"name": "Biela", "settings": {}}}
        reply = business_greeting.get_greeting(ctx, "David")
        assert reply.startswith("Hola David.")

    @pytest.mark.parametrize("name", ["Usuario", "Cliente", "User", "usuario", "CLIENTE", "", None])
    def test_skips_opener_for_placeholder_names(self, name):
        ctx = {"business": {"name": "Biela", "settings": {}}}
        reply = business_greeting.get_greeting(ctx, name)
        assert not reply.startswith("Hola ")

    def test_uses_custom_hours_text_when_present(self):
        ctx = {
            "business": {
                "name": "Biela",
                "settings": {"hours_text": "Abierto de 10 AM a 10 PM todos los días."},
            }
        }
        reply = business_greeting.get_greeting(ctx, None)
        assert "Abierto de 10 AM a 10 PM todos los días." in reply
        # Default Biela hours must NOT appear when custom hours are set.
        assert "5:30 PM a 10:00 PM" not in reply

    def test_falls_back_to_legacy_hours_when_missing(self):
        ctx = {"business": {"name": "Biela", "settings": {}}}
        reply = business_greeting.get_greeting(ctx, None)
        assert "5:30 PM a 10:00 PM" in reply

    def test_no_business_context_uses_all_defaults(self):
        reply = business_greeting.get_greeting(None, None)
        assert "BIELA FAST FOOD" in reply
        assert "https://gixlink.com/Biela" in reply

    def test_empty_business_context_uses_all_defaults(self):
        reply = business_greeting.get_greeting({}, None)
        assert "BIELA FAST FOOD" in reply
