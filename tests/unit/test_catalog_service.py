"""Unit tests for app/services/catalog_service.py."""

from unittest.mock import patch

import pytest

from app.services import catalog_service


BIZ = "biz-1"


RAW_PRODUCT = {
    "id": "prod-uuid-1",
    "business_id": "biz-1",   # internal — must be stripped
    "name": "Barracuda",
    "price": 18000,
    "currency": "COP",
    "description": "  Hamburguesa grande  ",
    "category": "HAMBURGUESAS",
    "matched_by": "exact",
    "tags": ["carne", "queso"],
    "embedding": [0.1, 0.2],  # internal — must be stripped
    "metadata": {"sku": "BAR-1"},  # internal — must be stripped
}


class TestListCategories:
    def test_returns_list_from_catalog_cache(self):
        with patch("app.services.catalog_service.catalog_cache.list_categories") as m:
            m.return_value = ["BEBIDAS", "HAMBURGUESAS"]
            result = catalog_service.list_categories(BIZ)
        assert result == ["BEBIDAS", "HAMBURGUESAS"]
        m.assert_called_once_with(BIZ)

    def test_empty_business_id_returns_empty(self):
        result = catalog_service.list_categories("")
        assert result == []

    def test_none_from_cache_returns_empty_list(self):
        with patch("app.services.catalog_service.catalog_cache.list_categories") as m:
            m.return_value = None
            assert catalog_service.list_categories(BIZ) == []


class TestListProducts:
    def test_returns_normalized_products(self):
        with patch(
            "app.services.catalog_service.catalog_cache.list_products_with_fallback"
        ) as m:
            m.return_value = [RAW_PRODUCT]
            result = catalog_service.list_products(BIZ)
        assert len(result) == 1
        p = result[0]
        # Public shape — fixed field set.
        assert set(p.keys()) == {
            "id", "name", "price", "currency", "description",
            "category", "matched_by", "tags",
        }
        assert p["name"] == "Barracuda"
        assert p["price"] == 18000.0
        assert p["currency"] == "COP"
        assert p["description"] == "Hamburguesa grande"  # trimmed
        assert p["tags"] == ["carne", "queso"]

    def test_empty_description_becomes_none(self):
        raw = {**RAW_PRODUCT, "description": "   "}
        with patch(
            "app.services.catalog_service.catalog_cache.list_products_with_fallback"
        ) as m:
            m.return_value = [raw]
            result = catalog_service.list_products(BIZ)
        assert result[0]["description"] is None

    def test_category_filter_is_passed_through(self):
        with patch(
            "app.services.catalog_service.catalog_cache.list_products_with_fallback"
        ) as m:
            m.return_value = []
            catalog_service.list_products(BIZ, category="BEBIDAS")
        m.assert_called_once_with(business_id=BIZ, category="BEBIDAS")

    def test_none_category_becomes_empty_string_for_fallback(self):
        with patch(
            "app.services.catalog_service.catalog_cache.list_products_with_fallback"
        ) as m:
            m.return_value = []
            catalog_service.list_products(BIZ, category=None)
        m.assert_called_once_with(business_id=BIZ, category="")

    def test_empty_business_id_returns_empty(self):
        assert catalog_service.list_products("") == []


class TestSearchProducts:
    def test_delegates_to_product_order_service(self):
        with patch(
            "app.services.catalog_service.product_order_service.search_products"
        ) as m:
            m.return_value = [RAW_PRODUCT]
            result = catalog_service.search_products(BIZ, "barracuda")
        assert len(result) == 1
        assert result[0]["name"] == "Barracuda"
        m.assert_called_once_with(
            business_id=BIZ, query="barracuda", limit=20, unique=False,
        )

    def test_respects_unique_and_limit(self):
        with patch(
            "app.services.catalog_service.product_order_service.search_products"
        ) as m:
            m.return_value = []
            catalog_service.search_products(BIZ, "x", limit=5, unique=True)
        m.assert_called_once_with(business_id=BIZ, query="x", limit=5, unique=True)

    @pytest.mark.parametrize("query", ["", "   ", None])
    def test_empty_query_returns_empty(self, query):
        assert catalog_service.search_products(BIZ, query or "") == []

    def test_empty_business_id_returns_empty(self):
        assert catalog_service.search_products("", "barracuda") == []


class TestGetProduct:
    def test_lookup_by_id(self):
        with patch(
            "app.services.catalog_service.product_order_service.get_product"
        ) as m:
            m.return_value = RAW_PRODUCT
            result = catalog_service.get_product(BIZ, product_id="prod-uuid-1")
        assert result is not None
        assert result["name"] == "Barracuda"
        m.assert_called_once_with(
            product_id="prod-uuid-1", product_name=None, business_id=BIZ,
        )

    def test_lookup_by_name(self):
        with patch(
            "app.services.catalog_service.product_order_service.get_product"
        ) as m:
            m.return_value = RAW_PRODUCT
            catalog_service.get_product(BIZ, product_name="barracuda")
        m.assert_called_once_with(
            product_id=None, product_name="barracuda", business_id=BIZ,
        )

    def test_not_found_returns_none(self):
        with patch(
            "app.services.catalog_service.product_order_service.get_product"
        ) as m:
            m.return_value = None
            assert catalog_service.get_product(BIZ, product_name="nope") is None

    def test_missing_both_identifiers_returns_none(self):
        assert catalog_service.get_product(BIZ) is None
        assert catalog_service.get_product(BIZ, product_id="", product_name="") is None

    def test_empty_business_id_returns_none(self):
        assert catalog_service.get_product("", product_name="x") is None
