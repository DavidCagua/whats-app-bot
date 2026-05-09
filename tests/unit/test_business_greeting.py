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
            # Compound greetings — production trace 2026-05-09 showed
            # the LLM-only fallback misclassifying these into ``order``.
            # Deterministic regex must catch them so we don't depend on
            # prompt fidelity.
            "hola buenas noches",
            "hola buenas tardes",
            "hola buenos días",
            "hola buenas",
            "buenas hola",
            "hola qué tal",
            "hola que tal",
            "hola qué más",
            "buenas qué más",
            "hey hola",
            "hola, qué tal",
            "buenas tardes hola",
            "hola cómo estás?",
            "Hola, cómo estás",
            "hola cómo están",
            "buenos días qué hubo",
            "Hola Buenas Noches",
        ],
    )
    def test_compound_greeting_variants_match(self, msg):
        assert business_greeting.is_pure_greeting(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "",
            "   ",
            None,
            "hola quiero una barracuda",
            "buenas, a qué hora abren",
            "quiero pedir",
            "dame una coca",
            "tienes barracuda",
            "a qué hora abren",
            # Greeting + emoji / extra word should NOT fast-path either.
            "hola 😊",
            "hola amigo",
            "hola un domicilio",
            "buenas tardes una hamburguesa",
            "hola necesito ayuda",
            # Greeting prefix + product / question — the substantive
            # half decides routing, not the greeting.
            "buenos días tienen barracuda",
            "hola, qué precio tiene la picada",
        ],
    )
    def test_non_pure_greeting_rejected(self, msg):
        assert business_greeting.is_pure_greeting(msg) is False


class TestGetGreeting:
    """
    Plain-text greeting — used when the business has no Twilio CTA
    template configured. Body must mirror the CTA `rendered_body` so the
    customer sees consistent copy across both paths; the menu URL is
    appended on its own line because plain text has no button.
    """

    def test_uses_business_name_from_context(self):
        ctx = {"business": {"name": "Mi Restaurante", "settings": {"menu_url": "https://x.test/menu"}}}
        reply = business_greeting.get_greeting(ctx, "Juan")
        assert "Mi Restaurante" in reply
        assert "https://x.test/menu" in reply

    def test_body_matches_cta_format(self):
        # Same headline as CTA `rendered_body`, plus URL appended.
        ctx = {"business": {"name": "Biela", "settings": {"menu_url": "https://x.test/menu"}}}
        reply = business_greeting.get_greeting(ctx, "David")
        assert reply == (
            "Hola David 👋 Bienvenido a Biela 🍔🔥\n"
            "¿Qué se te antoja hoy? Estamos listos para ayudarte\n\n"
            "https://x.test/menu"
        )

    def test_prepends_real_customer_name(self):
        ctx = {"business": {"name": "Biela", "settings": {}}}
        reply = business_greeting.get_greeting(ctx, "David")
        assert reply.startswith("Hola David 👋 Bienvenido a Biela")

    @pytest.mark.parametrize("name", ["Usuario", "Cliente", "User", "usuario", "CLIENTE", "", None])
    def test_uses_anonymous_hola_for_placeholder_names(self, name):
        ctx = {"business": {"name": "Biela", "settings": {}}}
        reply = business_greeting.get_greeting(ctx, name)
        # Anonymous opener mirrors CTA: "Hola 👋 Bienvenido..." — never
        # echoes the placeholder name ("Cliente", "Usuario", etc.).
        assert reply.startswith("Hola 👋 Bienvenido a Biela")
        for placeholder in ("Cliente", "Usuario", "User"):
            assert placeholder not in reply.split("\n", 1)[0]

    def test_no_hours_line_in_greeting(self):
        # Hours moved out of the greeting to match the CTA body. They
        # remain available via business_info_service for explicit
        # "a qué hora abren" CS questions.
        ctx = {
            "business": {
                "name": "Biela",
                "settings": {"hours_text": "Abierto 10 AM a 10 PM."},
            }
        }
        reply = business_greeting.get_greeting(ctx, None)
        assert "Abierto 10 AM" not in reply
        assert "5:30 PM" not in reply
        assert "horario" not in reply.lower()

    def test_no_business_context_uses_all_defaults(self):
        reply = business_greeting.get_greeting(None, None)
        assert "BIELA FAST FOOD" in reply
        assert "https://gixlink.com/Biela" in reply

    def test_empty_business_context_uses_all_defaults(self):
        reply = business_greeting.get_greeting({}, None)
        assert "BIELA FAST FOOD" in reply


class TestCtaWelcomePayload:
    """The Twilio CTA path: button-card welcome via Content Template."""

    BIELA_TWILIO_CTX = {
        "provider": "twilio",
        "business": {
            "name": "Biela",
            "settings": {"welcome_content_sid": "HXabc123"},
        },
    }

    def test_returns_none_when_provider_not_twilio(self):
        ctx = {
            "provider": "meta",
            "business": {
                "name": "Biela",
                "settings": {"welcome_content_sid": "HXabc123"},
            },
        }
        assert business_greeting.cta_welcome_payload(ctx, "David") is None

    def test_returns_none_when_no_content_sid(self):
        ctx = {
            "provider": "twilio",
            "business": {"name": "Biela", "settings": {}},
        }
        assert business_greeting.cta_welcome_payload(ctx, "David") is None

    def test_known_name_emits_named_opener(self):
        out = business_greeting.cta_welcome_payload(self.BIELA_TWILIO_CTX, "David")
        assert out["content_sid"] == "HXabc123"
        assert out["variables"] == {"1": "Biela", "2": "Hola David "}
        assert out["rendered_body"].startswith("Hola David 👋 Bienvenido a Biela")
        # Schedule and menu URL are intentionally absent from the new body.
        assert "5:30 PM" not in out["rendered_body"]
        assert "gixlink" not in out["rendered_body"]

    @pytest.mark.parametrize("name", ["Cliente", "usuario", "User", "", None])
    def test_unknown_name_emits_anonymous_opener(self, name):
        out = business_greeting.cta_welcome_payload(self.BIELA_TWILIO_CTX, name)
        assert out["variables"] == {"1": "Biela", "2": "Hola "}
        # Body still says "Hola" — never echoes the placeholder.
        assert out["rendered_body"].startswith("Hola 👋 Bienvenido a Biela")
        assert "Cliente" not in out["rendered_body"]


# ──────────────────────────────────────────────────────────────────
# Closed-greeting (order-availability gate) tests
# ──────────────────────────────────────────────────────────────────

class TestClosedGreeting:
    """
    When ``business_info_service.is_taking_orders_now`` says the shop
    is closed, the greeting should announce it inline. Two paths:

    - get_greeting (plain text): always appends the closed sentence.
    - cta_welcome_payload: uses ``welcome_closed_content_sid`` if set,
      else returns None to force plain-text fallback.
    """

    _CLOSED_GATE_TODAY = {
        "can_take_orders": False,
        "reason": "closed",
        "opens_at": None,
        "next_open_dow": 5,           # Friday
        "next_open_time": __import__("datetime").time(9, 0),
        "now_local": __import__("datetime").datetime(2026, 5, 7, 18, 0),
    }
    _OPEN_GATE = {
        "can_take_orders": True,
        "reason": "open",
        "opens_at": None,
        "next_open_dow": None,
        "next_open_time": None,
        "now_local": None,
    }

    BIELA_OPEN_ONLY_CTX = {
        "provider": "twilio",
        "business": {
            "name": "Biela",
            "settings": {"welcome_content_sid": "HXopen123"},
        },
    }
    BIELA_BOTH_SIDS_CTX = {
        "provider": "twilio",
        "business": {
            "name": "Biela",
            "settings": {
                "welcome_content_sid": "HXopen123",
                "welcome_closed_content_sid": "HXclosed456",
            },
        },
    }

    # ── get_greeting (plain text) ────────────────────────────────

    def test_get_greeting_open_unchanged(self):
        out = business_greeting.get_greeting(
            self.BIELA_OPEN_ONLY_CTX, "Yisela", gate=self._OPEN_GATE,
        )
        assert "cerrados" not in out.lower()
        assert "antoja hoy" in out  # original open-state copy

    def test_get_greeting_closed_appends_sentence(self):
        out = business_greeting.get_greeting(
            self.BIELA_OPEN_ONLY_CTX, "Yisela", gate=self._CLOSED_GATE_TODAY,
        )
        assert "cerrados" in out.lower()
        assert "Hola Yisela" in out
        # Tail invites browsing while closed — not "antoja hoy" pressure.
        assert "menú" in out.lower() or "duda" in out.lower()
        assert "antoja hoy" not in out

    def test_get_greeting_no_gate_defaults_to_open(self):
        """Legacy callers passing no gate get the existing open behavior."""
        out = business_greeting.get_greeting(self.BIELA_OPEN_ONLY_CTX, "Yisela")
        assert "cerrados" not in out.lower()

    # ── cta_welcome_payload ──────────────────────────────────────

    def test_cta_open_uses_open_sid(self):
        out = business_greeting.cta_welcome_payload(
            self.BIELA_OPEN_ONLY_CTX, "Yisela", gate=self._OPEN_GATE,
        )
        assert out is not None
        assert out["content_sid"] == "HXopen123"
        assert out["kind"] == "open_cta"
        assert "cerrados" not in out["rendered_body"].lower()

    def test_cta_closed_with_closed_sid_uses_closed_template(self):
        out = business_greeting.cta_welcome_payload(
            self.BIELA_BOTH_SIDS_CTX, "Yisela", gate=self._CLOSED_GATE_TODAY,
        )
        assert out is not None
        assert out["content_sid"] == "HXclosed456"
        assert out["kind"] == "closed_cta"
        # Variables include {{3}} for the closed sentence.
        assert "3" in out["variables"]
        assert "cerrados" in out["variables"]["3"].lower()
        # Rendered body matches the body we'll persist for the inbox UI.
        assert "Hola Yisela" in out["rendered_body"]
        assert "cerrados" in out["rendered_body"].lower()

    def test_cta_closed_without_closed_sid_returns_none(self):
        """When no closed-state SID is configured, return None so the
        caller falls back to the plain-text greeting (which appends the
        closed sentence inline via get_greeting)."""
        out = business_greeting.cta_welcome_payload(
            self.BIELA_OPEN_ONLY_CTX, "Yisela", gate=self._CLOSED_GATE_TODAY,
        )
        assert out is None

    def test_cta_no_gate_defaults_to_open(self):
        out = business_greeting.cta_welcome_payload(
            self.BIELA_OPEN_ONLY_CTX, "Yisela",
        )
        assert out is not None
        assert out["content_sid"] == "HXopen123"
        assert out["kind"] == "open_cta"
