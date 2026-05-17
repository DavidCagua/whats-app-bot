"""
Smoke tests for the customer-service agent (tool-calling architecture).

Targets:
  - Sentinel parsing (FINAL / HANDOFF) round-trips cleanly.
  - Each @tool returns the expected sentinel shape for representative
    handler results.
  - Agent fast-paths fire before the LLM loop (order-closed, out-of-zone).
  - Pre-loop safety nets hand off to the order agent for the patterns
    that historically misrouted (price-of-product, despedida post-pedido).
  - cancel_order guard refuses without an explicit keyword AND without
    a cancellable placed order.
  - The dispatch loop terminates on a FINAL sentinel without a second
    LLM iteration.
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.agents.customer_service_agent import CustomerServiceAgent
from app.orchestration import customer_service_flow as csf
from app.orchestration.turn_context import TurnContext
from app.services import cs_tools


BIZ_CTX = {
    "business_id": "biz-1",
    "business": {"name": "Biela", "settings": {"hours_text": "Lun-Vie 5PM"}},
}


def _tool_ctx(**overrides):
    """Per-turn context dict the tools read from."""
    base = {
        "wa_id": "+573001234567",
        "business_id": "biz-1",
        "business_context": BIZ_CTX,
        "session": None,
        "turn_ctx": None,
        "message_body": "",
    }
    base.update(overrides)
    return base


def _tool_call(name, args, call_id="c1"):
    return {"name": name, "args": args or {}, "id": call_id, "type": "tool_call"}


# ── Sentinel helpers ───────────────────────────────────────────────────


class TestSentinelHelpers:
    def test_parse_final_strips_prefix(self):
        assert cs_tools.parse_final("FINAL|hola") == "hola"

    def test_parse_final_returns_none_for_non_final(self):
        assert cs_tools.parse_final("HANDOFF|to=order") is None
        assert cs_tools.parse_final("plain text") is None
        assert cs_tools.parse_final("") is None

    def test_parse_handoff_extracts_dict(self):
        out = cs_tools.parse_handoff("HANDOFF|to=order|segment=hi|reason=x")
        assert out == {"to": "order", "segment": "hi", "reason": "x"}

    def test_parse_handoff_returns_none_for_non_handoff(self):
        assert cs_tools.parse_handoff("FINAL|hola") is None
        assert cs_tools.parse_handoff("") is None


# ── Tool wrappers ──────────────────────────────────────────────────────


class TestGetBusinessInfoTool:
    def test_known_field_returns_final_with_template(self):
        # hours uses the `{value}` template (no preamble).
        token = cs_tools.set_tool_context(_tool_ctx())
        try:
            result = cs_tools.get_business_info.invoke({
                "field": "hours",
                "injected_business_context": _tool_ctx(),
            })
        finally:
            cs_tools.reset_tool_context(token)
        assert result.startswith("FINAL|")
        assert "Lun-Vie 5PM" in cs_tools.parse_final(result)

    def test_missing_field_returns_plain_text_for_llm(self):
        # The INFO_MISSING shape is NOT a FINAL sentinel — the LLM has
        # to compose the apology (with the business voice) from it.
        ctx = {
            "business_id": "biz-1",
            "business": {"name": "X", "settings": {}},  # no hours_text
        }
        ictx = _tool_ctx(business_context=ctx)
        token = cs_tools.set_tool_context(ictx)
        try:
            result = cs_tools.get_business_info.invoke({
                "field": "hours",
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)
        assert not result.startswith("FINAL|")
        assert not result.startswith("HANDOFF|")
        assert "INFO_MISSING" in result


class TestGetPaymentInfoTool:
    """get_payment_info returns a structured PAYMENT_INFO block (NOT a FINAL
    rendered reply). The CS agent's LLM reads the block and composes the
    Spanish response to the customer in the next iteration, filtering by
    what the customer asked or by session fulfillment.

    These tests pin the block's contract — shape, completeness, accurate
    session-mode signal — and stay out of LLM phrasing territory.
    """

    _BIELA_SETTINGS = {
        "payment_methods": [
            {"name": "Efectivo", "contexts": ["delivery_on_fulfillment", "on_site_on_fulfillment"]},
            {"name": "Tarjeta", "contexts": ["on_site_on_fulfillment"]},
            {
                "name": "Nequi",
                "contexts": [
                    "delivery_pay_now", "delivery_on_fulfillment",
                    "on_site_pay_now", "on_site_on_fulfillment",
                ],
            },
            {"name": "Transferencia", "contexts": ["delivery_pay_now", "on_site_pay_now"]},
            {"name": "Llave BreB", "contexts": ["delivery_pay_now", "on_site_pay_now"]},
        ],
        "payment_destinations": {
            "Nequi": "300 123 4567 (Biela SAS)",
            "Transferencia": "Bancolombia 123-456789-00",
        },
    }

    def _ctx(self, fulfillment_type=None, settings=None):
        ctx = {
            "business_id": "biz-1",
            "business": {
                "name": "Biela",
                "settings": self._BIELA_SETTINGS if settings is None else settings,
            },
        }
        session = None
        if fulfillment_type is not None:
            session = {"order_context": {"fulfillment_type": fulfillment_type}}
        return _tool_ctx(business_context=ctx, session=session)

    def _invoke(self, ictx):
        token = cs_tools.set_tool_context(ictx)
        try:
            return cs_tools.get_payment_info.invoke({
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)

    # ── Block shape ──

    def test_returns_plain_text_not_final(self):
        # The tool must NOT short-circuit the dispatch loop — the LLM has to
        # compose the reply, so the result is plain text without a FINAL
        # sentinel.
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        assert not result.startswith("FINAL|")
        assert not result.startswith("HANDOFF|")
        assert result.startswith("PAYMENT_INFO")

    def test_includes_methods_section(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        assert "Métodos aceptados" in result

    def test_includes_instructions_for_llm(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        assert "INSTRUCCIONES" in result
        # Instructions cover the main branches the LLM must follow.
        assert "domicilio" in result.lower()
        assert "local" in result.lower()

    # ── Methods listing ──

    def test_all_configured_methods_appear(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        for name in ("Efectivo", "Tarjeta", "Nequi", "Transferencia", "Llave BreB"):
            assert name in result, name

    def test_tarjeta_shows_local_only(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        # The Tarjeta line must indicate local-only — the LLM uses this to
        # tell a delivery customer "Tarjeta solo en el local".
        tarjeta_line = next(
            ln for ln in result.splitlines() if ln.startswith("- Tarjeta:")
        )
        assert "local" in tarjeta_line
        assert "domicilio" not in tarjeta_line

    def test_nequi_shows_both_fulfillments(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        nequi_line = next(
            ln for ln in result.splitlines() if ln.startswith("- Nequi:")
        )
        assert "domicilio" in nequi_line
        assert "local" in nequi_line

    def test_method_contexts_show_timings(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        nequi_line = next(
            ln for ln in result.splitlines() if ln.startswith("- Nequi:")
        )
        # Nequi has all four contexts on; both timings should surface.
        assert "al recibir" in nequi_line
        assert "por adelantado" in nequi_line

    def test_efectivo_shows_only_at_fulfillment(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        efectivo_line = next(
            ln for ln in result.splitlines() if ln.startswith("- Efectivo:")
        )
        assert "al recibir" in efectivo_line
        assert "al pagar" in efectivo_line
        # Efectivo is never pay-now in Biela's config.
        assert "por adelantado" not in efectivo_line

    # ── Session-mode signal ──

    def test_session_mode_delivery(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        assert "Modo del pedido actual: domicilio" in result

    @pytest.mark.parametrize("ft", ["pickup", "dine_in", "on_site"])
    def test_session_mode_on_site_variants(self, ft):
        result = self._invoke(self._ctx(fulfillment_type=ft))
        assert "Modo del pedido actual: local" in result

    def test_session_mode_unknown_when_no_session(self):
        result = self._invoke(self._ctx(fulfillment_type=None))
        assert "Modo del pedido actual: desconocido" in result

    # ── Destinations ──

    def test_destinations_section_present_with_pay_now_methods(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        # Header is colon-terminated; tells it apart from the INSTRUCCIONES
        # body which mentions the same phrase as a noun.
        assert "Datos para pago adelantado:" in result
        # Configured destinations are surfaced verbatim.
        assert "300 123 4567 (Biela SAS)" in result
        assert "Bancolombia 123-456789-00" in result

    def test_destination_missing_marker_for_unconfigured(self):
        # Llave BreB is a pay_now method but has no destination configured.
        result = self._invoke(self._ctx(fulfillment_type="delivery"))
        breb_destination_line = next(
            ln for ln in result.splitlines() if ln.startswith("- Llave BreB:") and "(sin datos" in ln
        )
        assert "sin datos configurados" in breb_destination_line

    def test_destinations_section_absent_when_no_pay_now_methods(self):
        # If no method has pay_now contexts, the destinations section is
        # omitted entirely.
        settings = {
            "payment_methods": [
                {"name": "Efectivo", "contexts": ["delivery_on_fulfillment", "on_site_on_fulfillment"]},
                {"name": "Tarjeta", "contexts": ["on_site_on_fulfillment"]},
            ],
        }
        result = self._invoke(self._ctx(fulfillment_type="delivery", settings=settings))
        # The section header (colon-terminated) must NOT appear. The
        # phrase still shows up inside the INSTRUCCIONES body, so match
        # on the header form specifically.
        assert "Datos para pago adelantado:" not in result

    # ── Fallbacks ──

    def test_no_methods_configured_graceful_fallback(self):
        result = self._invoke(self._ctx(fulfillment_type="delivery", settings={}))
        # The block still starts with PAYMENT_INFO and tells the LLM to
        # apologize without making things up.
        assert result.startswith("PAYMENT_INFO")
        assert "no tiene métodos de pago" in result.lower()
        assert "INSTRUCCIONES" in result


class TestGetOrderStatusTool:
    def test_no_order_returns_final(self):
        with patch.object(csf.order_lookup_service, "get_latest_order", return_value=None):
            ictx = _tool_ctx()
            token = cs_tools.set_tool_context(ictx)
            try:
                result = cs_tools.get_order_status.invoke({
                    "injected_business_context": ictx,
                })
            finally:
                cs_tools.reset_tool_context(token)
        assert result.startswith("FINAL|")
        assert "No tengo registro" in cs_tools.parse_final(result)

    def test_active_cart_returns_handoff_to_order(self):
        ictx = _tool_ctx(session={
            "order_context": {"items": [{"name": "Barracuda", "quantity": 1}]},
        })
        token = cs_tools.set_tool_context(ictx)
        try:
            with patch.object(csf.order_lookup_service, "get_latest_order") as m:
                result = cs_tools.get_order_status.invoke({
                    "injected_business_context": ictx,
                })
                m.assert_not_called()  # short-circuits before DB lookup
        finally:
            cs_tools.reset_tool_context(token)
        parsed = cs_tools.parse_handoff(result)
        assert parsed is not None
        assert parsed["to"] == "order"
        assert parsed["reason"] == "mi_pedido_active_cart"


class TestSelectListedPromoTool:
    def test_unique_match_returns_handoff_with_promo_id(self):
        ictx = _tool_ctx(session={
            "agent_contexts": {"customer_service": {"last_listed_promos": [
                {"id": "p1", "name": "Honey Burger Combo"},
                {"id": "p2", "name": "Familiar"},
            ]}},
        })
        token = cs_tools.set_tool_context(ictx)
        try:
            result = cs_tools.select_listed_promo.invoke({
                "selector": "primera",
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)
        parsed = cs_tools.parse_handoff(result)
        assert parsed is not None
        assert parsed["to"] == "order"
        assert parsed["promo_id"] == "p1"


class TestGetPromosTool:
    """
    `get_promos` lists active promos and, when there are upcoming ones
    this week, names them too. The upcoming entries must include the
    day each promo applies — without it, the customer reads "Dos Misuri
    con papas" without context and can't tell whether it applies today
    or some other day. Production 2026-05-11 / Biela: "También hay
    Dos Misuri con papas" misled the customer into asking for Misuri
    the same day.
    """

    def _patch_buckets(self, active, upcoming):
        from app.orchestration.customer_service_flow import _summarize_promo_for_listing
        # _handle_get_promos goes through promotion_service.list_promos_for_listing
        # and then summarizes each entry. We mock the underlying service
        # so the summarizer (which builds schedule_label from days_of_week)
        # runs end-to-end.
        return patch(
            "app.services.promotion_service.list_promos_for_listing",
            return_value={"active_now": active, "upcoming": upcoming},
        )

    def test_active_plus_upcoming_includes_day_when_flag_set(self):
        """When the agent explicitly opts into upcoming (customer asked
        about the rest of the week), the upcoming line names the day."""
        active = [
            {
                "id": "p1", "name": "Dos Oregon con papas",
                "fixed_price": 39900, "days_of_week": [1, 7],
            },
        ]
        upcoming = [
            {
                "id": "p2", "name": "Dos Misuri con papas",
                "fixed_price": 39900,
                "days_of_week": [3],  # Wednesday
                "next_active_day": 3,
            },
        ]
        ictx = _tool_ctx()
        token = cs_tools.set_tool_context(ictx)
        try:
            with self._patch_buckets(active, upcoming):
                result = cs_tools.get_promos.invoke({
                    "include_upcoming_other_days": True,
                    "injected_business_context": ictx,
                })
        finally:
            cs_tools.reset_tool_context(token)
        text = cs_tools.parse_final(result)
        assert text is not None
        # Active promo present.
        assert "Dos Oregon con papas" in text
        # Upcoming line names the day so the customer knows when.
        assert "Dos Misuri con papas (miércoles)" in text or (
            "Dos Misuri con papas" in text and "miércoles" in text
        )

    def test_active_plus_upcoming_hides_upcoming_by_default(self):
        """Default behavior: when today has promos, do NOT proactively
        list other-day promos. The customer asked about promos, not
        about the rest of the week — keep the answer focused."""
        active = [
            {
                "id": "p1", "name": "Dos Oregon con papas",
                "fixed_price": 39900, "days_of_week": [1, 7],
            },
        ]
        upcoming = [
            {
                "id": "p2", "name": "Dos Misuri con papas",
                "fixed_price": 39900,
                "days_of_week": [3],
                "next_active_day": 3,
            },
        ]
        ictx = _tool_ctx()
        token = cs_tools.set_tool_context(ictx)
        try:
            with self._patch_buckets(active, upcoming):
                result = cs_tools.get_promos.invoke({
                    "injected_business_context": ictx,
                })
        finally:
            cs_tools.reset_tool_context(token)
        text = cs_tools.parse_final(result)
        assert text is not None
        assert "Dos Oregon con papas" in text
        assert "Dos Misuri" not in text
        assert "También hay otras" not in text


# ── Agent fast-paths ───────────────────────────────────────────────────


class TestOrderClosedFastPath:
    def test_renders_deterministic_reply_without_llm(self):
        agent = CustomerServiceAgent()
        with patch(
            "app.agents.customer_service_agent.business_info_service.compute_open_status",
            return_value={
                "has_data": True, "is_open": False,
                "closes_at": None, "opens_at": None,
                "next_open_dow": None, "next_open_time": None,
                "now_local": None,
            },
        ), patch(
            "app.agents.customer_service_agent.business_info_service.format_open_status_sentence",
            return_value="Por ahora estamos cerrados.",
        ), patch(
            "app.agents.customer_service_agent.business_info_service.is_fully_closed_today",
            return_value=False,
        ), patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch.object(
            agent, "_llm",
        ) as fake_llm:
            out = agent.execute(
                message_body="quiero pedir",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
                handoff_context={"reason": "order_closed", "has_active_cart": False},
            )
            fake_llm.invoke.assert_not_called()  # fast-path skipped the LLM
        assert "estamos cerrados" in out["message"].lower()


class TestOutOfZoneFastPath:
    def test_renders_redirect_with_city_and_phone(self):
        agent = CustomerServiceAgent()
        with patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch.object(agent, "_llm") as fake_llm:
            out = agent.execute(
                message_body="quiero a Medellín",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
                handoff_context={
                    "reason": "out_of_zone",
                    "city": "Medellín",
                    "phone": "+573001112233",
                },
            )
            fake_llm.invoke.assert_not_called()
        assert "Medellín" in out["message"]
        assert "+573001112233" in out["message"]


class TestCancelOrderGuard:
    """
    The cancel_order safety gate now lives INSIDE the tool body
    (cs_tools.cancel_order) so no caller — agent, dispatcher, or future
    code path — can bypass it. Tests invoke the tool directly with a
    per-turn context that carries `turn_ctx` + `message_body`.
    """

    def _invoke(self, message_body, turn_ctx):
        ictx = _tool_ctx(message_body=message_body, turn_ctx=turn_ctx)
        token = cs_tools.set_tool_context(ictx)
        try:
            return cs_tools.cancel_order.invoke({
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)

    def test_refuses_without_explicit_keyword(self):
        # turn_ctx has a cancellable order — only the keyword guard should fire.
        tctx = TurnContext(has_recent_cancellable_order=True)
        out = self._invoke("gracias bro", tctx)
        assert out is not None
        assert "no_cancel_keyword" in out

    def test_refuses_without_cancellable_order(self):
        # Real cancel verb but nothing to cancel.
        tctx = TurnContext(has_recent_cancellable_order=False)
        out = self._invoke("cancela mi pedido", tctx)
        assert out is not None
        assert "no_cancellable_order" in out

    def test_passes_when_both_conditions_hold(self):
        # Both guards open; the tool body runs _handle_cancel_order which
        # in turn hits order_lookup_service. Mock it to return None so we
        # land in the RESULT_KIND_NO_ORDER → "no tengo registro" branch
        # — proves the guard did NOT short-circuit.
        tctx = TurnContext(has_recent_cancellable_order=True)
        with patch(
            "app.orchestration.customer_service_flow.order_lookup_service.get_latest_order",
            return_value=None,
        ):
            out = self._invoke("cancela mi pedido", tctx)
        assert not out.startswith("REFUSED"), (
            f"Guard incorrectly fired for valid cancel intent: {out!r}"
        )


class TestDespedidaSafetyNet:
    def test_post_order_gracias_hands_off_to_order(self):
        agent = CustomerServiceAgent()
        # latest_order_status set — turn is right after place_order.
        tctx = TurnContext(latest_order_status="confirmed")
        out = agent._pre_loop_safety_nets("gracias", BIZ_CTX, tctx)
        assert out is not None
        assert out["handoff"]["to"] == "order"
        assert out["handoff"]["context"]["reason"] == "despedida_post_pedido_misroute"

    def test_no_latest_status_means_no_handoff(self):
        agent = CustomerServiceAgent()
        # No placed order in turn_ctx — plain "gracias" is just chat.
        tctx = TurnContext(latest_order_status=None)
        # Mute the other safety-net branches by patching the helpers
        # they call so we isolate the despedida check.
        with patch("app.orchestration.router._deterministic_price_of_product", return_value=False):
            out = agent._pre_loop_safety_nets("gracias", BIZ_CTX, tctx)
        assert out is None


# ── Dispatch loop ──────────────────────────────────────────────────────


class TestDispatchLoopTerminatesOnFinal:
    def test_final_sentinel_returns_text_without_second_iteration(self):
        agent = CustomerServiceAgent()
        # Model calls get_business_info once. The tool returns a FINAL
        # sentinel, so the loop must terminate immediately — the LLM
        # is NOT invoked a second time.
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[_tool_call("get_business_info", {"field": "hours"})],
        )
        with patch.object(agent, "_llm") as fake_llm, patch(
            "app.agents.customer_service_agent.conversation_service.store_conversation_message"
        ), patch(
            "app.orchestration.router._deterministic_price_of_product", return_value=False,
        ), patch(
            "app.orchestration.router._expand_stuck_articles", side_effect=lambda m, _l: m,
        ):
            fake_llm.invoke.return_value = tool_call_msg
            out = agent.execute(
                message_body="a qué hora abren",
                wa_id="+573001234567", name="",
                business_context=BIZ_CTX, conversation_history=[],
            )
            # Exactly ONE iteration: tool ran, FINAL sentinel terminated the loop.
            assert fake_llm.invoke.call_count == 1
        assert out["message"]  # final text was set
        assert "Lun-Vie 5PM" in out["message"]
        assert out["state_update"] == {"active_agents": ["customer_service"]}


class TestHandoffPaymentProofTool:
    """
    The handoff_payment_proof tool disables the bot and returns a
    deterministic thank-you. The LLM decides WHEN to call it (visual
    classification of the image) — the tool body itself is unconditional.
    """

    def _invoke(self):
        ictx = _tool_ctx()
        token = cs_tools.set_tool_context(ictx)
        try:
            return cs_tools.handoff_payment_proof.invoke({
                "injected_business_context": ictx,
            })
        finally:
            cs_tools.reset_tool_context(token)

    def test_returns_final_thank_you(self):
        with patch.object(
            cs_tools.conversation_agent_service, "set_agent_enabled",
        ) as fake_set:
            result = self._invoke()
        assert result.startswith("FINAL|"), (
            "handoff_payment_proof must return a FINAL sentinel so the "
            f"dispatch loop terminates without LLM redraft. Got: {result!r}"
        )
        text = cs_tools.parse_final(result)
        assert text is not None
        assert "comprobante" in text.lower()
        assert "asesor" in text.lower() or "verifica" in text.lower()
        # Bot was disabled with the right reason tag.
        fake_set.assert_called_once()
        args, kwargs = fake_set.call_args
        assert args[2] is False, "agent_enabled must be set to False"
        assert kwargs.get("handoff_reason") == "payment_proof"

    def test_disable_failure_still_returns_thank_you(self):
        # If the DB write fails (network/etc.), the customer should still
        # see the thank-you — silent failure beats double-replying.
        with patch.object(
            cs_tools.conversation_agent_service, "set_agent_enabled",
            side_effect=RuntimeError("db down"),
        ):
            result = self._invoke()
        assert result.startswith("FINAL|"), (
            "Tool must still terminate the turn even when set_agent_enabled "
            f"raises. Got: {result!r}"
        )

    def test_tool_is_bound_to_cs_agent(self):
        # Surface regression: tool must be in the cs_tools tuple, otherwise
        # the LLM never sees it.
        names = {t.name for t in cs_tools.cs_tools}
        assert "handoff_payment_proof" in names
