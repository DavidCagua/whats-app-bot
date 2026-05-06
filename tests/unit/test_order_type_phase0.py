"""Phase 0 of the pickup-on-site flow: schema + serializer surfacing.

Pure additive: these tests pin the new contract so a later phase can't
silently regress the default. No DB or network calls.
"""

import pytest

from app.database import product_order_service as pos
from app.database.models import Order


class TestNormalizeOrderType:
    """`normalize_order_type` is the single validator the create-order
    path uses before persisting. Anything that returns None must be
    rejected upstream — the helper's contract is "valid string or None"."""

    def test_delivery_passes_through(self):
        assert pos.normalize_order_type("delivery") == "delivery"

    def test_pickup_passes_through(self):
        assert pos.normalize_order_type("pickup") == "pickup"

    def test_uppercase_is_normalized(self):
        assert pos.normalize_order_type("PICKUP") == "pickup"
        assert pos.normalize_order_type("Delivery") == "delivery"

    def test_whitespace_is_trimmed(self):
        assert pos.normalize_order_type("  pickup  ") == "pickup"

    def test_none_defaults_to_delivery(self):
        # Backwards compat: every existing caller passes None → delivery.
        assert pos.normalize_order_type(None) == "delivery"

    def test_empty_string_defaults_to_delivery(self):
        assert pos.normalize_order_type("") == "delivery"
        assert pos.normalize_order_type("   ") == "delivery"

    def test_unknown_value_returns_none(self):
        # Unknown values must NOT silently coerce to a default. Caller
        # surfaces a user-visible error instead of writing bad data.
        assert pos.normalize_order_type("dineIn") is None
        assert pos.normalize_order_type("takeout") is None
        assert pos.normalize_order_type("foo") is None

    def test_constants_match_exposed_set(self):
        assert pos.VALID_ORDER_TYPES == {pos.ORDER_TYPE_DELIVERY, pos.ORDER_TYPE_PICKUP}
        assert pos.ORDER_TYPE_DELIVERY == "delivery"
        assert pos.ORDER_TYPE_PICKUP == "pickup"


class TestOrderToDictSerialization:
    """`Order.to_dict()` MUST surface `order_type` so downstream surfaces
    (CS responses, admin payloads, status notifications) can branch on
    it without re-querying. Defaulting an unset attribute to 'delivery'
    keeps backward compat with rows that pre-date the migration column
    backfill."""

    def test_explicit_pickup_is_preserved(self):
        order = Order()
        order.order_type = "pickup"
        d = order.to_dict()
        assert d["order_type"] == "pickup"

    def test_explicit_delivery_is_preserved(self):
        order = Order()
        order.order_type = "delivery"
        d = order.to_dict()
        assert d["order_type"] == "delivery"

    def test_unset_attribute_defaults_to_delivery(self):
        # SQLAlchemy column defaults are applied on flush, not __init__.
        # Until flush, `order_type` is None — to_dict must coerce to the
        # historical behavior so older rows / pre-flush instances still
        # serialize cleanly.
        order = Order()
        d = order.to_dict()
        assert d["order_type"] == "delivery"
