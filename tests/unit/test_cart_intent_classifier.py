"""
Unit tests for the deterministic cart-mutation classifier.

The classifier sits in front of the planner LLM. When it produces a
confident match, the order agent calls set_cart_items directly with the
computed target list and the LLM only needs to compose the reply. When
it returns None, the planner runs as normal.

These tests pin down both the confident-match patterns and the
fall-through cases. A regression that starts firing on greetings or
adds is far worse than missing a borderline restatement, so we're
strict about the negative cases.
"""

import pytest

from app.services.cart_intent_classifier import classify_cart_mutation


# Sample cart fixtures — kept tiny so tests stay readable.
AL_PASTOR = {"product_id": "pid-al-pastor", "name": "AL PASTOR", "price": 27000, "quantity": 2}
MEXICAN = {"product_id": "pid-mexican", "name": "MEXICAN BURGER", "price": 27000, "quantity": 3}
BARRACUDA = {"product_id": "pid-barracuda", "name": "BARRACUDA", "price": 28000, "quantity": 2}
DENVER = {"product_id": "pid-denver", "name": "DENVER", "price": 27000, "quantity": 1}


# ---------------------------------------------------------------------------
# Multi-product restatement — the Katherin pattern
# ---------------------------------------------------------------------------


class TestRestatement:
    def test_katherin_es_multi_product(self):
        """Cart has 2x AL PASTOR. 'Es 1 al pastor y 1 Mexican burger' →
        target list with both items."""
        items = classify_cart_mutation(
            "Es 1 al pastor y 1 Mexican burger",
            [{**AL_PASTOR}],
        )
        assert items is not None
        # Two items, both at qty 1.
        assert len(items) == 2
        qtys = {it.get("product_name", "").lower(): it["quantity"] for it in items}
        assert qtys.get("al pastor") == 1 or qtys.get("AL PASTOR".lower()) == 1
        assert any("mexican" in (it.get("product_name") or "").lower() for it in items)

    def test_el_pedido_es_two_items(self):
        items = classify_cart_mutation(
            "El pedido es 2 mexican burger y 1 al pastor",
            [{**AL_PASTOR}, {**MEXICAN}],
        )
        assert items is not None
        assert len(items) == 2
        # Existing items must resolve via cart so product_id is preserved.
        ids = {it.get("product_id") for it in items}
        assert AL_PASTOR["product_id"] in ids
        assert MEXICAN["product_id"] in ids

    def test_son_with_totals_claim_stripped(self):
        """'Son 1 X y 1 Y solo dos en total' — the totals claim must not
        be parsed as another item."""
        items = classify_cart_mutation(
            "Son 1 al pastor y 1 Mexican burger solo dos en total",
            [{**AL_PASTOR}],
        )
        assert items is not None
        assert len(items) == 2

    def test_existing_item_notes_preserved(self):
        """When restatement names a product already in cart with notes,
        the notes carry through to the target list."""
        al_pastor_with_notes = {**AL_PASTOR, "notes": "sin cebolla"}
        items = classify_cart_mutation(
            "Es 1 al pastor y 1 Mexican burger",
            [al_pastor_with_notes],
        )
        assert items is not None
        for it in items:
            if "al pastor" in (it.get("product_name") or "").lower():
                assert it.get("notes") == "sin cebolla"


# ---------------------------------------------------------------------------
# Single-product correction — must preserve other cart items
# ---------------------------------------------------------------------------


class TestPartialCorrection:
    def test_solo_son_n_x_preserves_others(self):
        """Cart has 3x MEXICAN + 2x BARRACUDA. 'solo son 2 Mexican burger' →
        target list has MEXICAN at 2 AND BARRACUDA at 2 (untouched)."""
        items = classify_cart_mutation(
            "solo son 2 Mexican burger",
            [{**MEXICAN}, {**BARRACUDA}],
        )
        assert items is not None
        assert len(items) == 2
        qtys = {it["product_id"]: it["quantity"] for it in items}
        assert qtys[MEXICAN["product_id"]] == 2  # corrected
        assert qtys[BARRACUDA["product_id"]] == 2  # preserved at current qty

    def test_solo_una_x_with_other_items(self):
        """'solo una al pastor' — BARRACUDA must survive the correction."""
        items = classify_cart_mutation(
            "solo una al pastor",
            [{**AL_PASTOR}, {**BARRACUDA}],
        )
        assert items is not None
        assert len(items) == 2
        qtys = {it["product_id"]: it["quantity"] for it in items}
        assert qtys[AL_PASTOR["product_id"]] == 1
        assert qtys[BARRACUDA["product_id"]] == 2

    def test_que_sean_n_x(self):
        items = classify_cart_mutation(
            "que sean 3 mexican burger",
            [{**MEXICAN}, {**BARRACUDA}],
        )
        assert items is not None
        qtys = {it["product_id"]: it["quantity"] for it in items}
        assert qtys[MEXICAN["product_id"]] == 3
        assert qtys[BARRACUDA["product_id"]] == 2

    def test_partial_correction_product_not_in_cart_falls_through(self):
        """If the named product isn't in the cart, the classifier returns
        None — the LLM may still handle it (e.g. as an add)."""
        items = classify_cart_mutation(
            "solo una hamburguesa de pollo",
            [{**MEXICAN}],
        )
        assert items is None


# ---------------------------------------------------------------------------
# Fall-through cases — silence beats false positives
# ---------------------------------------------------------------------------


class TestFallThrough:
    @pytest.mark.parametrize(
        "msg",
        [
            "dame una barracuda",
            "agrégame una coca",
            "con una papas",
            "y una sprite por favor",
            "una hamburguesa al pastor",
            "qué tienes de bebidas",
            "hola, buenas",
            "para recoger",
            "es para llevar",  # "es" without an item — not a restatement
            "son las cinco",  # "son" + a non-product
            "",
            "   ",
        ],
    )
    def test_non_restatement_messages_return_none(self, msg):
        assert classify_cart_mutation(msg, []) is None

    def test_single_item_after_es_falls_through(self):
        """'es 1 al pastor' with one item could be either a partial
        correction OR a fresh order. We require 2+ items for restatement,
        and the partial-correction openers don't include 'es'. So this
        falls through to the LLM."""
        items = classify_cart_mutation("es 1 al pastor", [{**AL_PASTOR}])
        assert items is None

    def test_empty_cart_partial_correction_falls_through(self):
        """No cart → nothing to correct → fall through."""
        assert classify_cart_mutation("solo una barracuda", []) is None

    def test_ambiguous_cart_name_falls_through(self):
        """Cart has two MONTESA lines with different notes. Naming
        'MONTESA' is ambiguous — we don't pick silently."""
        line_a = {"product_id": "pid-montesa", "name": "MONTESA", "price": 27000, "quantity": 1, "notes": "sin queso"}
        line_b = {"product_id": "pid-montesa", "name": "MONTESA", "price": 27000, "quantity": 1, "notes": "extra champiñones"}
        items = classify_cart_mutation("solo una montesa", [line_a, line_b])
        assert items is None


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------


class TestRemoval:
    """Decrement-by-N and full-removal patterns: 'quita N X', 'quita la X',
    'elimina X', 'saca X', 'ya no quiero X'. Both leave every OTHER cart
    line intact at its current quantity."""

    def test_decrement_by_n(self):
        """Cart has 3 BARRACUDA. 'quita dos barracudas' → 1 BARRACUDA left."""
        items = classify_cart_mutation(
            "quita dos barracudas",
            [{**BARRACUDA, "quantity": 3}],
        )
        assert items is not None
        assert len(items) == 1
        assert items[0]["quantity"] == 1

    def test_decrement_exceeds_current_drops_line(self):
        """Cart has 2 BARRACUDA. 'quita tres barracudas' → BARRACUDA gone.
        Single-line cart with the only line dropped returns None so the
        LLM handles the 'cart emptied' chat state."""
        items = classify_cart_mutation(
            "quita tres barracudas",
            [{**BARRACUDA, "quantity": 2}],
        )
        assert items is None

    def test_full_removal_with_determiner(self):
        """Cart has BARRACUDA + AL PASTOR. 'quita la barracuda' → only
        AL PASTOR remains (BARRACUDA omitted from target list)."""
        items = classify_cart_mutation(
            "quita la barracuda",
            [{**BARRACUDA, "quantity": 1}, {**AL_PASTOR, "quantity": 1}],
        )
        assert items is not None
        assert len(items) == 1
        assert "al pastor" in items[0]["product_name"].lower()
        assert items[0]["quantity"] == 1

    def test_full_removal_preserves_other_quantities(self):
        """Cart has 3 BARRACUDA + 2 AL PASTOR. 'elimina la barracuda' →
        only 2 AL PASTOR remains."""
        items = classify_cart_mutation(
            "elimina la barracuda",
            [{**BARRACUDA, "quantity": 3}, {**AL_PASTOR, "quantity": 2}],
        )
        assert items is not None
        assert len(items) == 1
        assert "al pastor" in items[0]["product_name"].lower()
        assert items[0]["quantity"] == 2

    def test_decrement_preserves_other_lines(self):
        """Cart has 3 BARRACUDA + 2 AL PASTOR. 'quita dos barracudas' →
        1 BARRACUDA + 2 AL PASTOR (al pastor untouched)."""
        items = classify_cart_mutation(
            "quita dos barracudas",
            [{**BARRACUDA, "quantity": 3}, {**AL_PASTOR, "quantity": 2}],
        )
        assert items is not None
        assert len(items) == 2
        qtys = {it["product_name"].lower(): it["quantity"] for it in items}
        assert qtys.get("barracuda") == 1
        assert qtys.get("al pastor") == 2

    def test_full_removal_no_determiner(self):
        """'ya no quiero la barracuda' / 'saca la barracuda' work too."""
        items = classify_cart_mutation(
            "ya no quiero la barracuda",
            [{**BARRACUDA, "quantity": 1}, {**AL_PASTOR, "quantity": 1}],
        )
        assert items is not None
        assert len(items) == 1
        assert "al pastor" in items[0]["product_name"].lower()

    def test_unresolved_product_falls_through(self):
        """'quita la denver' when DENVER isn't in cart → None (LLM handles)."""
        items = classify_cart_mutation(
            "quita la denver",
            [{**BARRACUDA, "quantity": 1}],
        )
        assert items is None


class TestQuantityVocab:
    @pytest.mark.parametrize(
        "msg, expected_qty",
        [
            ("solo una al pastor", 1),
            ("solo dos al pastor", 2),
            ("solo tres al pastor", 3),
            ("solo 4 al pastor", 4),
            ("que sean cinco al pastor", 5),
        ],
    )
    def test_spanish_numbers(self, msg, expected_qty):
        items = classify_cart_mutation(msg, [{**AL_PASTOR}])
        assert items is not None
        assert items[0]["quantity"] == expected_qty
