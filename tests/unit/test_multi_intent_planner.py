"""
Unit tests for multi-intent planner dispatch.

Covers:
  - _extract_intents: legacy singleton + new multi-intent shape, cap at 3,
    malformed-input fallbacks.
  - _sort_intents_canonical: priority order (capture before mutation before
    state-advance) + ABANDON_CART sole-intent rule.
  - SUBMIT_DELIVERY_INFO empty-cart capture path: state stays GREETING /
    ORDERING when there's nothing to deliver yet (the Yisela 2026-05-06
    case — first message dumps product + PII, capture must not force
    COLLECTING_DELIVERY for an empty cart).
"""

from unittest.mock import patch, MagicMock

import pytest

from app.agents.order_agent import (
    _extract_intents,
    _match_cta_button,
    _sort_intents_canonical,
    MAX_INTENTS_PER_TURN,
)
from app.database.session_state_service import (
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
)
from app.orchestration.order_flow import (
    execute_order_intent,
    _save_pending_disambiguation,
    INTENT_SUBMIT_DELIVERY_INFO,
    RESULT_KIND_DELIVERY_STATUS,
)


# ---------------------------------------------------------------------------
# _extract_intents
# ---------------------------------------------------------------------------

class TestExtractIntents:
    def test_legacy_singleton_becomes_one_element_list(self):
        out = _extract_intents({"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA"}})
        assert out == [{"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA"}}]

    def test_new_multi_intent_shape(self):
        out = _extract_intents({"intents": [
            {"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA"}},
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {"name": "Yisela"}},
        ]})
        assert len(out) == 2
        assert out[0]["intent"] == "ADD_TO_CART"
        assert out[1]["intent"] == "SUBMIT_DELIVERY_INFO"

    def test_cap_at_max_intents(self):
        out = _extract_intents({"intents": [
            {"intent": "CHAT", "params": {}} for _ in range(10)
        ]})
        assert len(out) == MAX_INTENTS_PER_TURN

    def test_normalizes_intent_case_and_spaces(self):
        out = _extract_intents({"intent": "add to cart", "params": {}})
        assert out[0]["intent"] == "ADD_TO_CART"

    def test_empty_dict_falls_back_to_chat(self):
        assert _extract_intents({}) == [{"intent": "CHAT", "params": {}}]

    def test_non_dict_input_falls_back_to_chat(self):
        assert _extract_intents("garbage") == [{"intent": "CHAT", "params": {}}]
        assert _extract_intents(None) == [{"intent": "CHAT", "params": {}}]

    def test_malformed_entries_are_skipped(self):
        """
        Within the cap, malformed entries (non-dict, empty intent) are
        skipped silently while valid ones survive. Note: the cap is
        applied to the raw input BEFORE filtering, so malformed entries
        eat into the budget — this is intentional so an adversarial
        100-element list doesn't cause a full scan.
        """
        out = _extract_intents({"intents": [
            {"intent": "ADD_TO_CART", "params": {}},
            "not a dict",
            {"intent": "CHAT"},  # missing params defaults to {}
        ]})
        assert len(out) == 2
        assert out[0]["intent"] == "ADD_TO_CART"
        assert out[1]["intent"] == "CHAT"
        assert out[1]["params"] == {}

    def test_empty_intent_name_is_skipped(self):
        out = _extract_intents({"intents": [
            {"intent": "", "params": {}},
            {"intent": "ADD_TO_CART", "params": {}},
        ]})
        assert len(out) == 1
        assert out[0]["intent"] == "ADD_TO_CART"

    def test_all_malformed_falls_back_to_chat(self):
        out = _extract_intents({"intents": ["x", 1, None]})
        assert out == [{"intent": "CHAT", "params": {}}]


# ---------------------------------------------------------------------------
# _sort_intents_canonical
# ---------------------------------------------------------------------------

class TestSortIntentsCanonical:
    def test_submit_delivery_runs_before_add_to_cart(self):
        """Capture-style intents land first so subsequent intents see fresh profile."""
        out = _sort_intents_canonical([
            {"intent": "ADD_TO_CART", "params": {}},
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["SUBMIT_DELIVERY_INFO", "ADD_TO_CART"]

    def test_confirm_with_cart_mutation_drops_confirm(self):
        """
        CONFIRM paired with a cart-mutating intent (ADD/UPDATE/REMOVE)
        is dropped so the customer sees the recap before being asked to
        close. Mirrors the SUBMIT_DELIVERY_INFO + CONFIRM rule. Production
        2026-05-07 (David / 3177000722) saw "dos barracudas" emit
        [ADD_TO_CART, CONFIRM] and skip the recap step.
        """
        out = _sort_intents_canonical([
            {"intent": "CONFIRM", "params": {}},
            {"intent": "ADD_TO_CART", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["ADD_TO_CART"]

    def test_abandon_cart_strips_siblings(self):
        """ABANDON_CART is destructive and must run alone."""
        out = _sort_intents_canonical([
            {"intent": "ADD_TO_CART", "params": {}},
            {"intent": "ABANDON_CART", "params": {}},
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {}},
        ])
        assert out == [{"intent": "ABANDON_CART", "params": {}}]

    def test_three_intent_compound_drops_confirm_for_recap(self):
        """
        Realistic compound message: product + PII + 'asi esta bien'. The
        dedup drops CONFIRM when SUBMIT_DELIVERY_INFO is present so the
        customer sees the recap CTA before placing — the explicit confirm
        comes from tapping the button on the next turn.
        """
        out = _sort_intents_canonical([
            {"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA"}},
            {"intent": "CONFIRM", "params": {}},
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {"payment_method": "transferencia"}},
        ])
        assert [i["intent"] for i in out] == [
            "SUBMIT_DELIVERY_INFO",
            "ADD_TO_CART",
        ]

    def test_unknown_intent_falls_in_chat_priority(self):
        """Defensive: unknown intents shouldn't crash the sort."""
        out = _sort_intents_canonical([
            {"intent": "WEIRD", "params": {}},
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {}},
        ])
        assert out[0]["intent"] == "SUBMIT_DELIVERY_INFO"

    def test_confirm_dedupes_redundant_place_order(self):
        """
        Production regression (David / 3177000722, 2026-05-07): planner
        emitted [CONFIRM, PLACE_ORDER] for "asi esta bien". CONFIRM
        resolves to PROCEED_TO_CHECKOUT or PLACE_ORDER based on state, so
        emitting PLACE_ORDER alongside is redundant and the second step
        gets rejected by the allowlist after CONFIRM advanced state. The
        dedup drops PLACE_ORDER so only CONFIRM runs.
        """
        out = _sort_intents_canonical([
            {"intent": "CONFIRM", "params": {}},
            {"intent": "PLACE_ORDER", "params": {}},
        ])
        assert out == [{"intent": "CONFIRM", "params": {}}]

    def test_confirm_dedupes_redundant_proceed_to_checkout(self):
        out = _sort_intents_canonical([
            {"intent": "CONFIRM", "params": {}},
            {"intent": "PROCEED_TO_CHECKOUT", "params": {}},
        ])
        assert out == [{"intent": "CONFIRM", "params": {}}]

    def test_proceed_dedupes_redundant_place_order(self):
        """PROCEED + PLACE: PROCEED moves to COLLECTING_DELIVERY where
        PLACE isn't allowed yet, so the duplicate PLACE just produces a
        recovery. Drop it."""
        out = _sort_intents_canonical([
            {"intent": "PROCEED_TO_CHECKOUT", "params": {}},
            {"intent": "PLACE_ORDER", "params": {}},
        ])
        assert out == [{"intent": "PROCEED_TO_CHECKOUT", "params": {}}]

    def test_confirm_with_update_cart_item_drops_confirm(self):
        """
        UPDATE_CART_ITEM + CONFIRM: same shape as ADD + CONFIRM. The
        customer changed the cart; show them the new state before
        closing. Production 2026-05-07: "mejor una no más" → planner
        emitted [UPDATE_CART_ITEM, CONFIRM], which advanced state
        through the recap.
        """
        out = _sort_intents_canonical([
            {"intent": "UPDATE_CART_ITEM", "params": {}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["UPDATE_CART_ITEM"]

    def test_confirm_with_remove_from_cart_drops_confirm(self):
        out = _sort_intents_canonical([
            {"intent": "REMOVE_FROM_CART", "params": {}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["REMOVE_FROM_CART"]

    def test_confirm_with_add_promo_drops_confirm(self):
        out = _sort_intents_canonical([
            {"intent": "ADD_PROMO_TO_CART", "params": {}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["ADD_PROMO_TO_CART"]

    def test_confirm_alone_is_unaffected(self):
        """Singleton CONFIRM (e.g. CTA tap, "sí" reply) goes through
        unchanged — the dedup only fires on multi-intent emissions."""
        out = _sort_intents_canonical([
            {"intent": "CONFIRM", "params": {}},
        ])
        assert out == [{"intent": "CONFIRM", "params": {}}]

    def test_confirm_with_pure_browse_intent_is_kept(self):
        """Browse intents (VIEW_CART, GET_PRODUCT) aren't mutating —
        CONFIRM alongside them is fine."""
        out = _sort_intents_canonical([
            {"intent": "VIEW_CART", "params": {}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["VIEW_CART", "CONFIRM"]

    def test_submit_delivery_dedupes_confirm_to_keep_recap_step(self):
        """
        Production regression (David / 3177000722, 2026-05-07): customer
        typed "transferencia" to supply the missing payment field. Planner
        emitted [SUBMIT_DELIVERY_INFO, CONFIRM]. Without dedup, SUBMIT
        transitions state to READY_TO_PLACE then CONFIRM resolves to
        PLACE_ORDER — the order is placed instantly and the customer never
        sees the "Confirmar pedido / Cambiar algo" CTA recap. Drop CONFIRM
        so the data submission's result_kind=delivery_status fires the CTA
        path; the explicit confirm comes from the button tap on the next turn.
        """
        out = _sort_intents_canonical([
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {"payment_method": "transferencia"}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["SUBMIT_DELIVERY_INFO"]

    def test_submit_delivery_dedupes_confirm_with_address_only(self):
        """Same dedup applies whatever subset of fields SUBMIT carries."""
        out = _sort_intents_canonical([
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {"address": "Cl 20"}},
            {"intent": "CONFIRM", "params": {}},
        ])
        assert [i["intent"] for i in out] == ["SUBMIT_DELIVERY_INFO"]

    def test_submit_delivery_keeps_add_to_cart_sibling(self):
        """SUBMIT + ADD is the Yisela case — both must run; only CONFIRM is
        special-cased."""
        out = _sort_intents_canonical([
            {"intent": "SUBMIT_DELIVERY_INFO", "params": {"name": "Yisela"}},
            {"intent": "ADD_TO_CART", "params": {"product_name": "BARRACUDA"}},
        ])
        assert [i["intent"] for i in out] == ["SUBMIT_DELIVERY_INFO", "ADD_TO_CART"]


# ---------------------------------------------------------------------------
# SUBMIT_DELIVERY_INFO empty-cart capture path (Yisela regression)
# ---------------------------------------------------------------------------

class TestSubmitDeliveryEmptyCart:
    """
    When the customer dumps product + delivery PII in one message and the
    multi-intent dispatcher routes SUBMIT_DELIVERY_INFO before ADD_TO_CART,
    the executor sees an empty cart at the moment SUBMIT runs. Forcing a
    transition to COLLECTING_DELIVERY in that case would confuse the
    response generator into asking about delivery for an empty cart, and
    would also cause the subsequent ADD_TO_CART to need a re-open. The
    fix: with an empty cart, persist the data silently and stay in the
    current state.
    """

    def test_submit_with_empty_cart_stays_in_greeting(
        self, fake_session, wa_id, business_context,
    ):
        submit_tool = MagicMock()
        submit_tool.invoke.return_value = "OK_PARTIAL"

        # _cart_from_session is read inside the SUBMIT_DELIVERY_INFO branch
        # to decide whether to advance state — empty cart is the test signal.
        order_tools_mock = MagicMock()
        order_tools_mock._cart_from_session.return_value = {"items": [], "total": 0}

        with patch("app.orchestration.order_flow._find_tool", return_value=submit_tool), \
             patch("app.orchestration.order_flow.order_tools", order_tools_mock), \
             patch("app.orchestration.order_flow.product_order_service"), \
             patch(
                 "app.orchestration.order_flow._build_delivery_status",
                 return_value={
                     "name": "Yisela",
                     "address": "Cl 20 #42-105",
                     "phone": "3015349690",
                     "payment_method": "",
                     "all_present": False,
                     "missing": ["payment"],
                 },
             ), \
             patch(
                 "app.orchestration.order_flow.session_state_service",
                 fake_session,
             ):
            session = {"order_context": {"state": ORDER_STATE_GREETING}}
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_context["business_id"],
                business_context=business_context,
                session=session,
                intent=INTENT_SUBMIT_DELIVERY_INFO,
                params={
                    "name": "Yisela",
                    "address": "Cl 20 #42-105",
                    "phone": "3015349690",
                },
                message_body="Una BARRACUDA. Yisela. Cl 20 #42-105. 3015349690",
            )

        assert result.get("result_kind") == RESULT_KIND_DELIVERY_STATUS
        # Critical: state did NOT move to COLLECTING_DELIVERY despite partial
        # data, because the cart was empty.
        assert result["state_after"] == ORDER_STATE_GREETING
        ds = result.get("delivery_status") or {}
        assert ds.get("name") == "Yisela"
        assert ds.get("address") == "Cl 20 #42-105"
        assert ds.get("phone") == "3015349690"


# ---------------------------------------------------------------------------
# Pending disambiguation preserves quantity (limonada regression)
# ---------------------------------------------------------------------------

class TestDisambigPreservesQuantity:
    """
    Production regression (David / 3177000722, 2026-05-07): customer ordered
    "una barracuda y dos limonadas y una vimota". Multi-item add was
    ambiguous on "limonada" (4 catalog matches). Bot disambiguated, customer
    replied "limonada natural" — but the original quantity (2) was lost,
    cart got 1x natural instead of 2x. Fix: store ``requested_quantity``
    in the pending entry so the bypass path can restore it.
    """

    def test_save_pending_disambiguation_persists_quantity_and_notes(
        self, fake_session, wa_id, business_context,
    ):
        with patch("app.orchestration.order_flow.session_state_service", fake_session):
            _save_pending_disambiguation(
                wa_id=wa_id,
                business_id=business_context["business_id"],
                requested_name="limonada",
                options=[{"name": "Limonada natural", "product_id": "p1", "price": 6500}],
                requested_quantity=2,
                requested_notes="sin azúcar",
            )

        loaded = fake_session.load(wa_id, business_context["business_id"])
        pending = loaded["session"]["order_context"]["pending_disambiguation"]
        assert pending["requested_quantity"] == 2
        assert pending["requested_notes"] == "sin azúcar"
        assert len(pending["options"]) == 1

    def test_save_pending_disambiguation_omits_default_quantity(
        self, fake_session, wa_id, business_context,
    ):
        """qty=1 / no notes: don't pollute the pending entry — the bypass
        defaults to planner-emitted quantity in that case anyway."""
        with patch("app.orchestration.order_flow.session_state_service", fake_session):
            _save_pending_disambiguation(
                wa_id=wa_id,
                business_id=business_context["business_id"],
                requested_name="limonada",
                options=[{"name": "Limonada natural", "product_id": "p1"}],
            )

        loaded = fake_session.load(wa_id, business_context["business_id"])
        pending = loaded["session"]["order_context"]["pending_disambiguation"]
        assert "requested_quantity" not in pending
        assert "requested_notes" not in pending


# ---------------------------------------------------------------------------
# CTA quick-reply button shortcut (Luis regression)
# ---------------------------------------------------------------------------

class TestCtaButtonMatch:
    """
    Production regression (Luis / 3159280840, 2026-05-07): customer tapped
    "Confirmar pedido" on the CTA card. Planner saw the card's data recap
    in recent history and re-emitted those fields as SUBMIT_DELIVERY_INFO,
    looping the customer through the CTA twice. Fix: skip the planner
    entirely on known button-title matches and emit a deterministic intent.
    """

    def test_confirmar_pedido_maps_to_confirm(self):
        assert _match_cta_button("Confirmar pedido") == {"intent": "CONFIRM", "params": {}}

    def test_match_is_case_insensitive(self):
        assert _match_cta_button("confirmar pedido") == {"intent": "CONFIRM", "params": {}}
        assert _match_cta_button("CONFIRMAR PEDIDO") == {"intent": "CONFIRM", "params": {}}

    def test_match_trims_whitespace(self):
        assert _match_cta_button("  Confirmar pedido  ") == {"intent": "CONFIRM", "params": {}}

    def test_cambiar_algo_maps_to_chat(self):
        """The 'Cambiar algo' button routes to CHAT so the response composer
        asks what the customer would like to change."""
        assert _match_cta_button("Cambiar algo") == {"intent": "CHAT", "params": {}}

    def test_freeform_text_does_not_match(self):
        """Only exact button titles trigger the shortcut. Anything more
        elaborate goes through the LLM planner where the CONFIRMACIÓN rule
        handles it."""
        assert _match_cta_button("Confirmar pedido y agregar coca cola") is None
        assert _match_cta_button("Una BARRACUDA") is None

    def test_empty_input_returns_none(self):
        assert _match_cta_button("") is None
        assert _match_cta_button(None) is None
