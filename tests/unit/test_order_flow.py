"""
Unit tests for order_flow.py — state machine transitions and intent guards.
Tests the executor logic without any LLM or DB calls.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.database.session_state_service import (
    derive_order_state,
    ORDER_STATE_GREETING,
    ORDER_STATE_ORDERING,
    ORDER_STATE_COLLECTING_DELIVERY,
    ORDER_STATE_READY_TO_PLACE,
)
from app.orchestration.order_flow import (
    execute_order_intent,
    INTENT_GREET,
    INTENT_ADD_TO_CART,
    INTENT_VIEW_CART,
    INTENT_UPDATE_CART_ITEM,
    INTENT_REMOVE_FROM_CART,
    INTENT_PROCEED_TO_CHECKOUT,
    INTENT_SUBMIT_DELIVERY_INFO,
    INTENT_PLACE_ORDER,
    INTENT_CHAT,
    INTENT_GET_MENU_CATEGORIES,
    INTENT_LIST_PRODUCTS,
    INTENT_SEARCH_PRODUCTS,
    ALLOWED_INTENTS_BY_STATE,
    _normalize_product_name,
    _resolve_from_pending_disambiguation,
    _resolve_product_id_by_name,
)


# ---------------------------------------------------------------------------
# derive_order_state
# ---------------------------------------------------------------------------

class TestDeriveOrderState:
    """Test state derivation from order_context."""

    def test_empty_context_returns_greeting(self):
        assert derive_order_state(None) == ORDER_STATE_GREETING
        assert derive_order_state({}) == ORDER_STATE_GREETING

    # Case: context with items but no delivery info → ORDERING
    # Case: context with items + partial delivery info → ORDERING
    # Case: context with items + full delivery info (name, address, phone, payment) → READY_TO_PLACE
    # Case: context with explicit state field → returns that state directly
    # Case: context with unknown state field → falls through to derivation logic


# ---------------------------------------------------------------------------
# Intent guards (ALLOWED_INTENTS_BY_STATE)
# ---------------------------------------------------------------------------

class TestIntentGuards:
    """Test that intents are only allowed in the correct states."""

    def test_place_order_blocked_in_greeting(self, fake_session, wa_id, business_context):
        """
        PLACE_ORDER in GREETING is an invariant violation — it should not be
        reachable by the planner now that CONFIRM owns confirmation verbs.
        If it happens anyway (planner drift), the executor must return a
        soft recovery result (CHAT), not a user-facing error. The old
        behavior dead-ended users with "Esa acción no se puede hacer..."
        """
        session = {"order_context": {"state": ORDER_STATE_GREETING}}

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools"):
            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_context["business_id"],
                business_context=business_context,
                session=session,
                intent=INTENT_PLACE_ORDER,
            )

        # Recovery semantics: never surface rejections as user_error.
        assert result.get("result_kind") == "chat"
        assert result.get("error_kind") != "user_visible"
        assert result["state_after"] == ORDER_STATE_GREETING

    # Case: PROCEED_TO_CHECKOUT blocked in GREETING state
    # Case: VIEW_CART blocked in GREETING state (not in allowed list)
    # Case: REMOVE_FROM_CART blocked in GREETING state
    # Case: UPDATE_CART_ITEM blocked in GREETING state
    # Case: ADD_TO_CART allowed in GREETING state
    # Case: GREET allowed in GREETING state but not in ORDERING
    # Case: SUBMIT_DELIVERY_INFO blocked in ORDERING state
    # Case: PLACE_ORDER blocked in ORDERING state
    # Case: PLACE_ORDER allowed in READY_TO_PLACE state
    # Case: Menu browsing intents (GET_MENU_CATEGORIES, LIST_PRODUCTS, SEARCH_PRODUCTS) allowed in GREETING and ORDERING

    @pytest.mark.parametrize(
        "starting_state",
        [ORDER_STATE_READY_TO_PLACE, ORDER_STATE_COLLECTING_DELIVERY],
    )
    def test_add_to_cart_reopens_cart_from_post_cart_states(
        self, starting_state, fake_session, wa_id, business_context
    ):
        """
        A cart-mutating intent arriving after the user has moved past ORDERING
        (into COLLECTING_DELIVERY or READY_TO_PLACE) must not be rejected: the
        flow should drop back to ORDERING and execute the intent. Guards against
        the prod bug where users couldn't add items after starting checkout.
        """
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "prod-001", "name": "BARRACUDA", "quantity": 1, "price": 18000}],
                "total": 18000,
                "delivery_info": {
                    "name": "Luis", "address": "Calle 1", "phone": "+573001234567",
                    "payment_method": "efectivo",
                },
                "state": starting_state,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        fake_tool = MagicMock()
        fake_tool.invoke = MagicMock(return_value=None)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", return_value=fake_tool), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added", "items": [], "total": 36000}):
            mock_cart_log.side_effect = [
                {"items": [{"name": "BARRACUDA", "quantity": 1}], "total": 18000},
                {"items": [{"name": "BARRACUDA", "quantity": 1}, {"name": "LIMONADA", "quantity": 1}], "total": 23000},
            ]
            mock_tools._cart_from_session.return_value = {"items": [], "total": 23000}

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"product_name": "LIMONADA", "quantity": 1},
            )

        assert result["success"] is True
        assert result["result_kind"] == "cart_change"
        # State was re-opened, not stuck post-ORDERING
        stored = fake_session.load(wa_id, business_id)["session"]
        assert stored["order_context"]["state"] == ORDER_STATE_ORDERING
        fake_tool.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    """Test that successful tool execution causes correct state transitions."""

    # Case: ADD_TO_CART success in GREETING → transitions to ORDERING
    # Case: ADD_TO_CART success in ORDERING → stays in ORDERING
    # Case: PROCEED_TO_CHECKOUT in ORDERING with items → transitions to COLLECTING_DELIVERY
    # Case: PROCEED_TO_CHECKOUT in ORDERING with empty cart → rejected, stays in ORDERING
    # Case: SUBMIT_DELIVERY_INFO with all fields → transitions to READY_TO_PLACE
    # Case: SUBMIT_DELIVERY_INFO with partial fields → stays in COLLECTING_DELIVERY
    # Case: PLACE_ORDER success → resets to GREETING (context cleared)
    # Case: CHAT intent → no state change regardless of current state
    # Case: GREET intent → no state change


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestResolveFromPendingDisambiguation:
    """
    Unit tests for the bypass that resolves a product_name against saved
    disambiguation options without hitting search_products. This is what
    breaks the infinite disambiguation loop where "soda" is both an exact
    match and a prefix of other variants.
    """

    @staticmethod
    def _soda_pending(replacement: str = None):
        pending = {
            "requested_name": "soda",
            "options": [
                {"name": "Soda", "price": 4500, "product_id": "prod-soda"},
                {"name": "Soda Frutos rojos", "price": 15000, "product_id": "prod-soda-fr"},
                {"name": "Soda Uvilla y maracuyá", "price": 15000, "product_id": "prod-soda-uv"},
            ],
        }
        if replacement:
            pending["pending_replacement_product_id"] = replacement
        return pending

    def test_exact_name_resolves_generic_soda(self):
        """User replies 'Soda' — must map to the generic 'Soda' product_id."""
        pending = self._soda_pending()
        assert _resolve_from_pending_disambiguation(pending, "Soda") == "prod-soda"

    def test_case_and_accent_insensitive(self):
        """'soda frutos rojos' (lowercase, no accents) → Soda Frutos rojos."""
        pending = self._soda_pending()
        assert _resolve_from_pending_disambiguation(pending, "soda frutos rojos") == "prod-soda-fr"
        assert _resolve_from_pending_disambiguation(pending, "SODA UVILLA Y MARACUYA") == "prod-soda-uv"

    def test_no_pending_returns_none(self):
        assert _resolve_from_pending_disambiguation(None, "Soda") is None
        assert _resolve_from_pending_disambiguation({}, "Soda") is None
        assert _resolve_from_pending_disambiguation({"options": []}, "Soda") is None

    def test_no_name_returns_none(self):
        pending = self._soda_pending()
        assert _resolve_from_pending_disambiguation(pending, "") is None
        assert _resolve_from_pending_disambiguation(pending, None) is None

    def test_non_matching_name_returns_none(self):
        """'Cola' isn't in the saved soda options → fall back to search_products."""
        pending = self._soda_pending()
        assert _resolve_from_pending_disambiguation(pending, "Cola") is None

    def test_partial_name_match_does_not_resolve(self):
        """
        Only exact normalized-name equality wins. 'Soda frutos' (incomplete)
        should NOT resolve to Soda Frutos rojos — we don't want the bypass
        guessing when the planner wasn't explicit.
        """
        pending = self._soda_pending()
        assert _resolve_from_pending_disambiguation(pending, "Soda frutos") is None

    def test_option_without_product_id_is_skipped(self):
        """Legacy pending entries (no product_id) must not trigger the bypass."""
        pending = {
            "requested_name": "soda",
            "options": [{"name": "Soda", "price": 4500}],  # no product_id
        }
        assert _resolve_from_pending_disambiguation(pending, "Soda") is None


class TestUpdateCartItemSwap:
    """
    Variant swap path: UPDATE_CART_ITEM with new_product_name must atomically
    add the replacement and remove the old item, updating cart price.
    Regression for: "la soda que sea de frutos rojos" leaving cart at $4.500
    with a cosmetic note instead of swapping to $15.000.
    """

    def test_swap_calls_add_then_remove(self, fake_session, wa_id, business_context):
        from app.database.session_state_service import ORDER_STATE_ORDERING
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "prod-soda", "name": "Soda", "quantity": 1, "price": 4500}],
                "total": 4500,
                "state": ORDER_STATE_ORDERING,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        add_tool = MagicMock()
        add_tool.invoke = MagicMock(return_value=None)
        remove_tool = MagicMock()
        remove_tool.invoke = MagicMock(return_value=None)
        update_tool = MagicMock()
        update_tool.invoke = MagicMock(return_value=None)

        def _find_tool_mock(name):
            return {
                "add_to_cart": add_tool,
                "remove_from_cart": remove_tool,
                "update_cart_item": update_tool,
            }.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "replaced", "added": [], "removed": [], "items": [], "total": 15000}):
            mock_cart_log.side_effect = [
                {"items": [{"name": "Soda", "quantity": 1}], "total": 4500},
                {"items": [{"name": "Soda Frutos rojos", "quantity": 1}], "total": 15000},
            ]
            mock_tools._cart_from_session.return_value = {
                "items": [{"product_id": "prod-soda", "name": "Soda", "quantity": 1, "price": 4500}],
                "total": 4500,
            }

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_UPDATE_CART_ITEM,
                params={"product_name": "Soda", "new_product_name": "Soda Frutos rojos"},
            )

        assert result["success"] is True
        assert result["result_kind"] == "cart_change"
        # add_to_cart was called with the new product name
        assert add_tool.invoke.call_count == 1
        add_args = add_tool.invoke.call_args[0][0]
        assert add_args["product_name"] == "Soda Frutos rojos"
        # remove_from_cart was called with the OLD product_id
        assert remove_tool.invoke.call_count == 1
        rm_args = remove_tool.invoke.call_args[0][0]
        assert rm_args["product_id"] == "prod-soda"
        # update_cart_item (notes path) was NOT called — swap branch owns the update
        update_tool.invoke.assert_not_called()

    def test_swap_with_ambiguous_new_product_stashes_replacement(self, fake_session, wa_id, business_context):
        """
        If the new product is ambiguous, the old item must stay in the cart and
        the disambiguation response must carry pending_replacement_product_id
        so the bypass can complete the swap on the next turn.
        """
        from app.database.session_state_service import ORDER_STATE_ORDERING
        from app.database.product_order_service import AmbiguousProductError
        business_id = business_context["business_id"]
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [{"product_id": "prod-soda", "name": "Soda", "quantity": 1, "price": 4500}],
                "total": 4500,
                "state": ORDER_STATE_ORDERING,
            }},
        )
        session = fake_session.load(wa_id, business_id)["session"]

        matches = [
            {"id": "prod-soda-fr", "name": "Soda Frutos rojos", "price": 15000},
            {"id": "prod-soda-uv", "name": "Soda Uvilla y maracuyá", "price": 15000},
        ]

        add_tool = MagicMock()
        add_tool.invoke = MagicMock(side_effect=AmbiguousProductError(query="Soda Frutos", matches=matches))
        remove_tool = MagicMock()
        remove_tool.invoke = MagicMock(return_value=None)

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool, "remove_from_cart": remove_tool, "update_cart_item": MagicMock()}.get(name)

        saved_pending = {}
        original_save = fake_session.save
        def _capture_save(wa, biz, update):
            oc = (update or {}).get("order_context") or {}
            if "pending_disambiguation" in oc:
                saved_pending.update(oc["pending_disambiguation"])
            return original_save(wa, biz, update)

        with patch("app.orchestration.order_flow.session_state_service") as sss, \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [{"product_id": "prod-soda", "name": "Soda", "quantity": 1}], "total": 4500}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            sss.load = fake_session.load
            sss.save = _capture_save
            mock_tools._cart_from_session.return_value = {
                "items": [{"product_id": "prod-soda", "name": "Soda", "quantity": 1, "price": 4500}],
                "total": 4500,
            }

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_UPDATE_CART_ITEM,
                params={"product_name": "Soda", "new_product_name": "Soda Frutos"},
            )

        assert result["result_kind"] == "needs_clarification"
        # remove_from_cart was NOT called — old item stays until user chooses
        remove_tool.invoke.assert_not_called()
        # The old product_id was stashed for the next-turn bypass
        assert saved_pending.get("pending_replacement_product_id") == "prod-soda"


class TestNormalizeProductName:
    """Test _normalize_product_name fuzzy matching helper."""

    # Case: lowercase and strip whitespace
    # Case: collapse multiple spaces
    # Case: treat hyphens as spaces ("coca-cola" → "coca cola")
    # Case: empty string → empty string
    # Case: None → empty string


# ---------------------------------------------------------------------------
# Intent-to-tool mapping
# ---------------------------------------------------------------------------

class TestIntentToolMapping:
    """Test that intents are mapped to the correct tool with correct args."""

    # Case: INTENT_ADD_TO_CART maps to "add_to_cart" with product_name, quantity, notes
    # Case: INTENT_ADD_TO_CART with items[] list invokes add_to_cart multiple times (multi-item)
    # Case: INTENT_REMOVE_FROM_CART maps to "remove_from_cart" with product_id or product_name
    # Case: INTENT_UPDATE_CART_ITEM resolves product_id from product_name in cart
    # Case: INTENT_UPDATE_CART_ITEM with 2-item list (replace A with B) → reduce first, add second
    # Case: INTENT_LIST_PRODUCTS maps to "list_category_products" with category param
    # Case: INTENT_SEARCH_PRODUCTS maps to "search_products" with query param
    # Case: Unknown intent → returns error "no mapeado a herramienta"


# ---------------------------------------------------------------------------
# Multi-item ADD_TO_CART fault tolerance
#
# Regression for the Biela transcript (wa_id +573242261188, 2026-04-15):
# "Dame 1 jugo de mora en leche y 1 soda de frutos rojos". The planner
# emitted both items, but the executor's multi-item loop re-raised
# AmbiguousProductError on the first item and never processed the
# second — the soda was silently lost.
#
# New contract:
# - If ONE item in the batch is ambiguous and others succeed, the cart
#   keeps the successful adds, the ambiguity is persisted as
#   pending_disambiguation, and the result carries a
#   pending_clarification extra so the response generator can mention
#   both the partial success and the open question.
# - If nothing succeeds (every item was ambiguous or only one item was
#   passed and it was ambiguous), fall back to the old behavior: raise
#   out to the outer handler which builds a needs_clarification result.
# ---------------------------------------------------------------------------


class TestMultiItemAddToCartFaultTolerance:
    """Regression tests for the multi-item executor loop resilience."""

    @staticmethod
    def _seed_empty_ordering(fake_session, wa_id, business_id):
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": [],
                "total": 0,
                "state": ORDER_STATE_ORDERING,
            }},
        )
        return fake_session.load(wa_id, business_id)["session"]

    def test_first_item_ambiguous_second_item_exact_still_adds_second(
        self, fake_session, wa_id, business_context,
    ):
        """
        Two-item batch: first item is ambiguous (Biela "jugo de mora en
        leche" before Fix B would hit this shape; we simulate by making
        the add_to_cart mock raise for that name), second is an exact
        match. Expected: cart ends with the second item, disambiguation
        is persisted for the first, and the result_kind is cart_change
        carrying pending_clarification.
        """
        from app.database.product_order_service import AmbiguousProductError
        business_id = business_context["business_id"]
        session = self._seed_empty_ordering(fake_session, wa_id, business_id)

        ambiguous_matches = [
            {"id": "p-jleche", "name": "Jugos en leche", "price": 7500},
            {"id": "p-jagua",  "name": "Jugos en agua",  "price": 7500},
        ]

        add_invocations: list = []

        def _add_side_effect(args):
            add_invocations.append(args)
            name = (args.get("product_name") or "").lower()
            if "jugo" in name:
                raise AmbiguousProductError(query="jugo", matches=ambiguous_matches)
            # Anything else "succeeds" (tool would normally mutate session)
            return None

        add_tool = MagicMock()
        add_tool.invoke.side_effect = _add_side_effect

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        # Capture the pending_disambiguation that gets persisted via
        # session_state_service.save during the multi-item loop.
        saved_pending: dict = {}
        original_save = fake_session.save
        def _capture_save(wa, biz, update):
            oc = (update or {}).get("order_context") or {}
            if "pending_disambiguation" in oc:
                saved_pending.update(oc["pending_disambiguation"])
            return original_save(wa, biz, update)

        with patch("app.orchestration.order_flow.session_state_service") as sss, \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added",
                                 "added": [{"name": "Soda Frutos rojos", "quantity": 1}],
                                 "removed": [], "updated": [], "cart_after": [],
                                 "total_after": 15000}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            sss.load = fake_session.load
            sss.save = _capture_save
            mock_tools._cart_from_session.return_value = {
                "items": [{"product_id": "p-soda-fr", "name": "Soda Frutos rojos", "quantity": 1, "price": 15000}],
                "total": 15000,
            }
            mock_cart_log.side_effect = [
                {"items": [], "total": 0},                                             # cart_before
                {"items": [{"name": "Soda Frutos rojos", "quantity": 1}], "total": 15000},  # cart_after
            ]

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "jugo de mora en leche", "quantity": 1},
                    {"product_name": "Soda Frutos rojos",     "quantity": 1},
                ]},
            )

        # Both items were attempted (ambiguous first did NOT black-hole the second)
        attempted_names = [a.get("product_name") for a in add_invocations]
        assert "jugo de mora en leche" in attempted_names
        assert "Soda Frutos rojos" in attempted_names

        # cart_change came through (soda succeeded) with a pending_clarification
        # extra so the response generator can confirm + ask in one message.
        assert result["success"] is True
        assert result["result_kind"] == "cart_change"
        pending = result.get("pending_clarification") or {}
        option_names = [o.get("name") for o in (pending.get("options") or [])]
        assert "Jugos en leche" in option_names
        assert "Jugos en agua" in option_names
        assert pending.get("requested_name") == "jugo de mora en leche"

        # Pending disambiguation was persisted for the next-turn bypass resolver.
        assert [o.get("name") for o in saved_pending.get("options") or []] == [
            "Jugos en leche", "Jugos en agua",
        ]

    def test_all_items_ambiguous_falls_back_to_needs_clarification(
        self, fake_session, wa_id, business_context,
    ):
        """
        Two-item batch where EVERY item is ambiguous. Nothing succeeds,
        so the executor re-raises the first ambiguity and the outer
        handler builds a standard needs_clarification result. Guards
        the "at least one success" contract from above.
        """
        from app.database.product_order_service import AmbiguousProductError
        business_id = business_context["business_id"]
        session = self._seed_empty_ordering(fake_session, wa_id, business_id)

        add_tool = MagicMock()
        add_tool.invoke = MagicMock(side_effect=AmbiguousProductError(
            query="jugo",
            matches=[
                {"id": "p-jleche", "name": "Jugos en leche", "price": 7500},
                {"id": "p-jagua",  "name": "Jugos en agua",  "price": 7500},
            ],
        ))

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [], "total": 0}), \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "noop", "added": [], "removed": [],
                                 "updated": [], "cart_after": [], "total_after": 0}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {"items": [], "total": 0}

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "jugo de mora",   "quantity": 1},
                    {"product_name": "jugo de lulo",   "quantity": 1},
                ]},
            )

        # Both items tried; both raised. Outer handler builds the
        # needs_clarification result from the FIRST ambiguity.
        assert result["result_kind"] == "needs_clarification"
        assert add_tool.invoke.call_count == 2

    def test_first_item_not_found_second_item_exact_still_adds_second(
        self, fake_session, wa_id, business_context,
    ):
        """
        Regression for the Biela +573177000722 transcript (2026-04-16):
        "Un jugo de mango en leche y una club Colombia" — the jugo lane
        was not found (no mango product exists, and Fix B couldn't
        salvage it), so add_to_cart raised ProductNotFoundError. Before
        this fix the tool RETURNED an error STRING that the multi-item
        loop silently swallowed; the jugo disappeared with no trace in
        the response. After the fix, the error is raised, captured in
        multi_not_found, and surfaced via the cart_change result's
        `not_found` extra so the response can flag what was missing.
        """
        from app.database.product_order_service import ProductNotFoundError
        business_id = business_context["business_id"]
        session = self._seed_empty_ordering(fake_session, wa_id, business_id)

        add_invocations: list = []

        def _add_side_effect(args):
            add_invocations.append(args)
            name = (args.get("product_name") or "").lower()
            if "mango" in name:
                raise ProductNotFoundError(query=args.get("product_name") or "")
            return None  # successful add for everything else

        add_tool = MagicMock()
        add_tool.invoke.side_effect = _add_side_effect

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added",
                                 "added": [{"name": "Club Colombia", "quantity": 1}],
                                 "removed": [], "updated": [], "cart_after": [],
                                 "total_after": 7500}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {
                "items": [{"product_id": "p-club", "name": "Club Colombia", "quantity": 1, "price": 7500}],
                "total": 7500,
            }
            mock_cart_log.side_effect = [
                {"items": [], "total": 0},
                {"items": [{"name": "Club Colombia", "quantity": 1}], "total": 7500},
            ]

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "jugo de mango en leche", "quantity": 1},
                    {"product_name": "Club Colombia",          "quantity": 1},
                ]},
            )

        # Both items attempted — not_found on the first MUST NOT skip the second
        attempted_names = [a.get("product_name") for a in add_invocations]
        assert "jugo de mango en leche" in attempted_names
        assert "Club Colombia" in attempted_names

        # Partial success: cart_change carries not_found extra with the
        # missing item label so the response generator can flag it.
        assert result["success"] is True
        assert result["result_kind"] == "cart_change"
        assert "jugo de mango en leche" in (result.get("not_found") or [])

    def test_all_items_not_found_raises_to_user_error(
        self, fake_session, wa_id, business_context,
    ):
        """
        When every item in a multi-item batch is ProductNotFoundError
        and nothing succeeds, the executor re-raises so the outer
        handler emits a user_error result (not a silent noop cart).
        """
        from app.database.product_order_service import ProductNotFoundError
        business_id = business_context["business_id"]
        session = self._seed_empty_ordering(fake_session, wa_id, business_id)

        add_tool = MagicMock()

        def _not_found(args):
            raise ProductNotFoundError(query=args.get("product_name") or "")
        add_tool.invoke.side_effect = _not_found

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [], "total": 0}), \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "noop", "added": [], "removed": [],
                                 "updated": [], "cart_after": [], "total_after": 0}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {"items": [], "total": 0}

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "jugo de pitahaya", "quantity": 1},
                    {"product_name": "jugo de mangostino", "quantity": 1},
                ]},
            )

        assert result["success"] is False
        assert result["result_kind"] == "user_error"
        # Both missing items should appear in the user-visible error message
        msg = (result.get("error_message") or "").lower()
        assert "pitahaya" in msg
        assert "mangostino" in msg
        assert add_tool.invoke.call_count == 2

    def test_single_item_not_found_raises_to_user_error(
        self, fake_session, wa_id, business_context,
    ):
        """
        Single-item ADD_TO_CART with a not-found product must surface
        as a user_error, not a silent noop cart. Pinned here because
        the new ProductNotFoundError path is the replacement for the
        old "return '❌ Producto no encontrado'" string.
        """
        from app.database.product_order_service import ProductNotFoundError
        business_id = business_context["business_id"]
        session = self._seed_empty_ordering(fake_session, wa_id, business_id)

        add_tool = MagicMock()
        add_tool.invoke.side_effect = ProductNotFoundError(query="pitahaya con hielo")

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [], "total": 0}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {"items": [], "total": 0}

            result = execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"product_name": "pitahaya con hielo", "quantity": 1},
            )

        assert result["success"] is False
        assert result["result_kind"] == "user_error"
        assert "pitahaya" in (result.get("error_message") or "").lower()


# ---------------------------------------------------------------------------
# _resolve_product_id_by_name — name + notes matching
#
# Regression for the "quita el jugo de mango" bug where two cart items
# share the same base product name ("Jugos en leche") and differ only
# by notes (mora vs mango). The resolver must disambiguate by notes.
# ---------------------------------------------------------------------------


class TestResolveProductIdByName:
    """Test the 3-pass cart-item resolver used by UPDATE and REMOVE."""

    CART_ITEMS = [
        {"product_id": "pid-jl-mora",  "name": "Jugos en leche", "notes": "mora",  "price": 7500, "quantity": 1},
        {"product_id": "pid-jl-mango", "name": "Jugos en leche", "notes": "mango", "price": 7500, "quantity": 1},
        {"product_id": "pid-barracuda","name": "BARRACUDA",       "notes": "",      "price": 28000,"quantity": 1},
    ]

    @pytest.fixture(autouse=True)
    def _seed_cart(self, fake_session, wa_id, business_context):
        self.wa_id = wa_id
        self.business_id = business_context["business_id"]
        fake_session.save(
            wa_id, self.business_id,
            {"order_context": {
                "items": list(self.CART_ITEMS),
                "total": 43000,
                "state": ORDER_STATE_ORDERING,
            }},
        )
        self._patcher = patch("app.orchestration.order_flow.order_tools.session_state_service", fake_session)
        self._mock = self._patcher.start()
        # Also patch the session_state_service used by _cart_from_session via order_tools
        self._patcher2 = patch("app.services.order_tools.session_state_service", fake_session)
        self._patcher2.start()

    @pytest.fixture(autouse=True)
    def _stop_patches(self):
        yield
        try:
            self._patcher.stop()
        except Exception:
            pass
        try:
            self._patcher2.stop()
        except Exception:
            pass

    def test_exact_base_name_single_match(self):
        """'BARRACUDA' — only one item, exact match."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "BARRACUDA")
        assert pid == "pid-barracuda"

    def test_parenthetical_notes_disambiguate(self):
        """'Jugos en leche (mango)' — two items share base name, parens pick mango."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "Jugos en leche (mango)")
        assert pid == "pid-jl-mango"

    def test_parenthetical_notes_disambiguate_mora(self):
        """'Jugos en leche (mora)' — same pattern, picks mora."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "Jugos en leche (mora)")
        assert pid == "pid-jl-mora"

    def test_qualifier_phrase_matches_notes(self):
        """'jugo de mango' — qualifier 'mango' matches item with notes='mango'."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "jugo de mango")
        assert pid == "pid-jl-mango"

    def test_base_name_ambiguous_returns_first(self):
        """'Jugos en leche' — two matches, no qualifier → returns first (best effort)."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "Jugos en leche")
        assert pid in ("pid-jl-mora", "pid-jl-mango")

    def test_no_match_returns_none(self):
        """'Pizza' — not in cart → None."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "Pizza")
        assert pid is None

    def test_partial_substring_fallback(self):
        """'barracuda' (lowercase) matches via substring on 'BARRACUDA'."""
        pid = _resolve_product_id_by_name(self.wa_id, self.business_id, "barracuda")
        assert pid == "pid-barracuda"


# ---------------------------------------------------------------------------
# Multi-item ADD_TO_CART — notes passthrough + duplicate parens strip
# ---------------------------------------------------------------------------


class TestMultiItemNotesAndDuplicates:
    """
    Regressions from Biela +573177000722:
    - e1d528e: multi-item loop wasn't passing notes to add_to_cart
    - aacc657: duplicate check didn't strip parens from planner names
    """

    @staticmethod
    def _seed_ordering(fake_session, wa_id, business_id, items=None):
        fake_session.save(
            wa_id, business_id,
            {"order_context": {
                "items": list(items or []),
                "total": sum(it.get("price", 0) * it.get("quantity", 0) for it in (items or [])),
                "state": ORDER_STATE_ORDERING,
            }},
        )
        return fake_session.load(wa_id, business_id)["session"]

    def test_multi_item_passes_notes_to_add_to_cart(
        self, fake_session, wa_id, business_context,
    ):
        """
        e1d528e regression: 'denver sin salchicha y honey sin cebolla'
        must land with notes on both items.
        """
        business_id = business_context["business_id"]
        session = self._seed_ordering(fake_session, wa_id, business_id)

        invoked_args: list = []

        def _capture_invoke(args):
            invoked_args.append(dict(args))
            return None

        add_tool = MagicMock()
        add_tool.invoke.side_effect = _capture_invoke

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging",
                   return_value={"items": [], "total": 0}), \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added", "added": [], "removed": [],
                                 "updated": [], "cart_after": [], "total_after": 0}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {"items": [], "total": 0}

            execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "DENVER", "quantity": 1, "notes": "sin salchicha"},
                    {"product_name": "HONEY BURGER", "quantity": 1, "notes": "sin cebolla"},
                ]},
            )

        # Both items must have their notes forwarded to add_to_cart
        assert len(invoked_args) == 2
        denver_args = next(a for a in invoked_args if "DENVER" in (a.get("product_name") or ""))
        honey_args = next(a for a in invoked_args if "HONEY" in (a.get("product_name") or ""))
        assert denver_args["notes"] == "sin salchicha"
        assert honey_args["notes"] == "sin cebolla"

    def test_duplicate_check_strips_parenthetical_notes(
        self, fake_session, wa_id, business_context,
    ):
        """
        aacc657 regression: planner re-emits 'Jugos en leche (mango)'
        from the cart summary. The duplicate check must strip the parens
        and match 'Jugos en leche' in the existing cart → skip.
        """
        business_id = business_context["business_id"]
        existing = [
            {"product_id": "pid-jl", "name": "Jugos en leche", "notes": "mango",
             "price": 7500, "quantity": 1},
        ]
        session = self._seed_ordering(fake_session, wa_id, business_id, items=existing)

        invoked_args: list = []

        def _capture_invoke(args):
            invoked_args.append(dict(args))
            return None

        add_tool = MagicMock()
        add_tool.invoke.side_effect = _capture_invoke

        def _find_tool_mock(name):
            return {"add_to_cart": add_tool}.get(name)

        with patch("app.orchestration.order_flow.session_state_service", fake_session), \
             patch("app.orchestration.order_flow.order_tools") as mock_tools, \
             patch("app.orchestration.order_flow._find_tool", side_effect=_find_tool_mock), \
             patch("app.orchestration.order_flow._get_cart_for_logging") as mock_cart_log, \
             patch("app.orchestration.order_flow._build_cart_change",
                   return_value={"action": "added", "added": [{"name": "BARRACUDA", "quantity": 1}],
                                 "removed": [], "updated": [], "cart_after": [], "total_after": 0}), \
             patch("app.orchestration.order_flow._clear_pending_disambiguation"):
            mock_tools._cart_from_session.return_value = {"items": existing, "total": 7500}
            mock_cart_log.side_effect = [
                {"items": existing, "total": 7500},
                {"items": existing + [{"name": "BARRACUDA", "quantity": 1}], "total": 35500},
            ]

            execute_order_intent(
                wa_id=wa_id,
                business_id=business_id,
                business_context=business_context,
                session=session,
                intent=INTENT_ADD_TO_CART,
                params={"items": [
                    {"product_name": "Jugos en leche (mango)", "quantity": 1},
                    {"product_name": "BARRACUDA", "quantity": 1},
                ]},
            )

        # Jugos en leche (mango) should be SKIPPED (duplicate after parens strip),
        # only BARRACUDA should be invoked
        invoked_names = [a.get("product_name") for a in invoked_args]
        assert "BARRACUDA" in invoked_names
        assert not any("Jugos en leche" in (n or "") for n in invoked_names), \
            f"Duplicate was not skipped: {invoked_names}"
