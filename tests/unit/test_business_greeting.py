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

    # Fully closed today (no opening window at all — Sunday-style closure).
    # opens_at is None, today_had_window is False, next open is a different weekday.
    _CLOSED_GATE_TODAY = {
        "can_take_orders": False,
        "reason": "closed",
        "opens_at": None,
        "next_open_dow": 5,           # Friday
        "next_open_time": __import__("datetime").time(9, 0),
        "today_had_window": False,
        "now_local": __import__("datetime").datetime(2026, 5, 7, 18, 0),
    }
    # Mid-day break: today HAS a window but the customer is messaging
    # before it opens. opens_at is set, today_had_window=True.
    _CLOSED_GATE_BREAK = {
        "can_take_orders": False,
        "reason": "closed",
        "opens_at": __import__("datetime").time(17, 0),
        "next_open_dow": 4,           # Thursday (same weekday as now_local)
        "next_open_time": __import__("datetime").time(17, 0),
        "today_had_window": True,
        "now_local": __import__("datetime").datetime(2026, 5, 7, 14, 0),
    }
    # Past today's close — today HAD a window (e.g. Mon 9am-10pm) but
    # the customer is messaging at 10:38pm. opens_at is None because no
    # more openings remain today; the next open is tomorrow.
    # Production 2026-05-11 / Biela.
    _CLOSED_GATE_PAST_CLOSE = {
        "can_take_orders": False,
        "reason": "closed",
        "opens_at": None,
        "next_open_dow": 2,           # Tuesday
        "next_open_time": __import__("datetime").time(9, 0),
        "today_had_window": True,
        "now_local": __import__("datetime").datetime(2026, 5, 11, 22, 38),
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

    def test_cta_closed_mid_day_break_returns_none(self):
        """Mid-day break (today's window opens later) — no CTA, plain
        text only. The "Ver carta" button on the closed card encouraged
        customers to browse and build a cart they couldn't submit yet.
        Operator policy update 2026-05-11: any closed state → plain text."""
        out = business_greeting.cta_welcome_payload(
            self.BIELA_BOTH_SIDS_CTX, "Yisela", gate=self._CLOSED_GATE_BREAK,
        )
        assert out is None

    def test_cta_closed_past_close_returns_none(self):
        """Past today's close (today HAD a window) — no CTA either.
        Same browse-and-build risk as mid-day break."""
        out = business_greeting.cta_welcome_payload(
            self.BIELA_BOTH_SIDS_CTX, "Yisela", gate=self._CLOSED_GATE_PAST_CLOSE,
        )
        assert out is None

    def test_cta_fully_closed_today_returns_none_even_with_closed_sid(self):
        """On a fully-closed-today greeting (Sunday-style closure), the
        Twilio CTA is suppressed even when the closed SID is configured.
        Plain-text greeting handles the day — its 'Ver carta' button
        would encourage building a cart that fails at submit time."""
        out = business_greeting.cta_welcome_payload(
            self.BIELA_BOTH_SIDS_CTX, "Yisela", gate=self._CLOSED_GATE_TODAY,
        )
        assert out is None

    def test_cta_closed_without_closed_sid_returns_none(self):
        """When no closed-state SID is configured, return None so the
        caller falls back to the plain-text greeting (which appends the
        closed sentence inline via get_greeting)."""
        out = business_greeting.cta_welcome_payload(
            self.BIELA_OPEN_ONLY_CTX, "Yisela", gate=self._CLOSED_GATE_BREAK,
        )
        assert out is None

    def test_cta_no_gate_defaults_to_open(self):
        out = business_greeting.cta_welcome_payload(
            self.BIELA_OPEN_ONLY_CTX, "Yisela",
        )
        assert out is not None
        assert out["content_sid"] == "HXopen123"
        assert out["kind"] == "open_cta"

    # ── Fully-closed-today alt-contact + menu URL drop ───────────

    BIELA_WITH_ALT_CONTACT_CTX = {
        "provider": "twilio",
        "business": {
            "name": "Biela",
            "settings": {
                "welcome_content_sid": "HXopen123",
                "menu_url": "https://example.com/menu",
                "alt_branch_contact": {
                    "name": "Sede Las Cuadras",
                    "phone": "+573026722877",
                },
            },
        },
    }

    def test_get_greeting_fully_closed_appends_alt_contact_line(self):
        out = business_greeting.get_greeting(
            self.BIELA_WITH_ALT_CONTACT_CTX, "Yisela",
            gate=self._CLOSED_GATE_TODAY,
        )
        assert "Sede Las Cuadras" in out
        assert "+573026722877" in out
        assert "Si necesitas pedir hoy" in out

    def test_get_greeting_fully_closed_drops_menu_url(self):
        out = business_greeting.get_greeting(
            self.BIELA_WITH_ALT_CONTACT_CTX, "Yisela",
            gate=self._CLOSED_GATE_TODAY,
        )
        # Menu URL is intentionally suppressed on fully-closed-today
        # greetings — encourages the redirect over a self-serve loop.
        assert "example.com/menu" not in out

    def test_get_greeting_mid_day_break_drops_menu_url_no_alt_contact(self):
        """Mid-day break (opens later today) — menu URL is dropped
        (policy extended 2026-05-12: no menu URL on any closed state,
        same rationale as the closed CTA suppression). Alt-contact
        also doesn't fire because today HAS a window."""
        out = business_greeting.get_greeting(
            self.BIELA_WITH_ALT_CONTACT_CTX, "Yisela",
            gate=self._CLOSED_GATE_BREAK,
        )
        assert "example.com/menu" not in out
        assert "Sede Las Cuadras" not in out

    def test_get_greeting_fully_closed_without_alt_contact_no_suffix(self):
        out = business_greeting.get_greeting(
            self.BIELA_OPEN_ONLY_CTX, "Yisela",
            gate=self._CLOSED_GATE_TODAY,
        )
        assert "cerrados" in out.lower()
        assert "Sede Las Cuadras" not in out
        assert "Si necesitas pedir hoy" not in out

    # ── Past today's close — alt-branch MUST NOT appear ────────────

    def test_get_greeting_past_close_with_window_no_alt_contact(self):
        """
        Production 2026-05-11 / Biela: Monday 9am-10pm shop, message at
        10:38pm. The alt-branch line ("Si necesitas pedir hoy, escríbele
        a Sede Las Cuadras...") used to appear in the plain-text greeting
        because the old logic conflated "past close" with "no window
        today". today_had_window now distinguishes them. Menu URL is
        also dropped — closed-state policy.
        """
        out = business_greeting.get_greeting(
            self.BIELA_WITH_ALT_CONTACT_CTX, "David",
            gate=self._CLOSED_GATE_PAST_CLOSE,
        )
        # Closed sentence still appears (different weekday next open).
        assert "cerrados" in out.lower()
        # Alt-branch line must NOT appear — today wasn't a "no service" day.
        assert "Sede Las Cuadras" not in out
        assert "Si necesitas pedir hoy" not in out
        # Menu URL must NOT appear — no menu link on any closed state.
        assert "example.com/menu" not in out

    def test_cta_fully_closed_sentence_keeps_alt_contact(self):
        """
        Regression coverage in the other direction: on a truly fully-
        closed day, the alt-branch line still needs to appear when the
        CTA happens to be rendered (this path is normally suppressed by
        the fully_closed_today=True branch — but if a caller bypasses
        that suppression, the sentence itself should still be correct).
        """
        # Call _closed_sentence_from_gate directly because cta_welcome_payload
        # short-circuits to None on fully_closed_today.
        biz = self.BIELA_WITH_ALT_CONTACT_CTX["business"]
        sentence = business_greeting._closed_sentence_from_gate(
            self._CLOSED_GATE_TODAY, business=biz,
        )
        assert "Sede Las Cuadras" in sentence
        assert "Si necesitas pedir hoy" in sentence


class TestStatusFromGate:
    """
    DRY helper that synthesizes the compute_open_status shape from a
    gate dict. Production 2026-05-11 (Biela): the two callers
    (_closed_sentence_from_gate, _is_fully_closed_today_from_gate)
    used to inline-build their own dicts and one was updated for
    today_had_window while the other wasn't — the closed CTA's {{3}}
    variable then carried the alt-branch contact line for past-close-
    with-window cases. These tests pin the dict shape so the two
    callers can't drift again.
    """

    def test_threads_all_gate_fields(self):
        gate = {
            "can_take_orders": False,
            "reason": "closed",
            "opens_at": "10:00",
            "next_open_dow": 2,
            "next_open_time": "10:00",
            "today_had_window": True,
            "now_local": "2026-05-12T22:38:00-05:00",
        }
        assert business_greeting._status_from_gate(gate) == {
            "is_open": False,
            "has_data": True,
            "opens_at": "10:00",
            "closes_at": None,
            "next_open_dow": 2,
            "next_open_time": "10:00",
            "today_had_window": True,
            "now_local": "2026-05-12T22:38:00-05:00",
        }

    def test_today_had_window_defaults_to_false_when_missing(self):
        # Gates from older code paths may not populate the field —
        # default to the fully-closed posture (False) so alt-branch
        # contact applies for backward compatibility.
        gate = {"can_take_orders": False, "reason": "closed", "opens_at": "10:00"}
        assert business_greeting._status_from_gate(gate)["today_had_window"] is False

    def test_closes_at_is_always_none(self):
        # Not meaningful in a closed-state synthesis. Pinning to None
        # prevents accidental leakage if a future gate carries closes_at.
        gate = {
            "can_take_orders": False, "reason": "closed",
            "closes_at": "22:00", "today_had_window": True,
        }
        assert business_greeting._status_from_gate(gate)["closes_at"] is None

    def test_has_data_and_is_open_are_constants(self):
        # Caller MUST have already verified gate is the closed payload,
        # so has_data=True / is_open=False are invariant for this helper.
        gate = {"can_take_orders": False, "reason": "closed"}
        status = business_greeting._status_from_gate(gate)
        assert status["has_data"] is True
        assert status["is_open"] is False
