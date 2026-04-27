"""Unit tests for app/orchestration/router.py — greeting fast-path + LLM decomposition."""

from unittest.mock import patch, MagicMock

import pytest

from app.orchestration import router


BIELA_CONTEXT = {
    "business_id": "biela",
    "business": {
        "name": "Biela",
        "settings": {"menu_url": "https://x.test/menu"},
    },
}


def _mock_llm_returning(content: str):
    """Build a mock llm.invoke() that returns a LangChain-style response."""
    llm = MagicMock()
    response = MagicMock()
    response.content = content
    llm.invoke.return_value = response
    return llm


class TestRouterGreetingFastPath:
    @pytest.mark.parametrize("msg", ["hola", "buenas", "buenos días", "hey"])
    def test_pure_greeting_returns_direct_reply(self, msg):
        with patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route(msg, BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.direct_reply is not None
        assert "Biela" in result.direct_reply
        assert result.segments is None
        assert result.domain is None

    def test_greeting_includes_customer_name(self):
        result = router.route("hola", BIELA_CONTEXT, "David")
        assert result.direct_reply.startswith("Hola David.")


class TestRouterSingleSegmentClassification:
    @pytest.mark.parametrize(
        "raw,expected_domain",
        [
            ('{"segments": [{"domain": "order", "text": "quiero una barracuda"}]}', router.DOMAIN_ORDER),
            ('{"segments": [{"domain": "customer_service", "text": "a qué hora abren"}]}', router.DOMAIN_CUSTOMER_SERVICE),
            ('{"segments": [{"domain": "chat", "text": "gracias"}]}', router.DOMAIN_CHAT),
        ],
    )
    def test_parses_single_segment(self, raw, expected_domain):
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("whatever", BIELA_CONTEXT, "David")
        assert result.segments is not None
        assert len(result.segments) == 1
        assert result.segments[0][0] == expected_domain
        # Backward-compat: single segment exposes `domain` property.
        assert result.domain == expected_domain

    def test_strips_markdown_fences(self):
        mock_llm = _mock_llm_returning('```json\n{"segments": [{"domain": "order", "text": "x"}]}\n```')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain == router.DOMAIN_ORDER

    def test_extracts_json_from_wrapping_text(self):
        mock_llm = _mock_llm_returning('Resultado: {"segments": [{"domain": "chat", "text": "x"}]} listo.')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain == router.DOMAIN_CHAT

    def test_obsolete_catalog_domain_rejected(self):
        """`catalog` was retired (see docs/agents-vs-services.md).
        If the classifier ever emits it the segment must be skipped, leaving
        the segments list either filtered to the valid ones or None entirely."""
        mock_llm = _mock_llm_returning(
            '{"segments": ['
            '{"domain": "catalog", "text": "qué bebidas hay"},'
            '{"domain": "order", "text": "dame una coca"}'
            ']}'
        )
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        # Only the valid segment survives.
        assert result.segments == [(router.DOMAIN_ORDER, "dame una coca")]


class TestRouterMultiSegmentDecomposition:
    def test_two_segments_different_domains(self):
        raw = (
            '{"segments": ['
            '{"domain": "order", "text": "dame una barracuda"},'
            '{"domain": "customer_service", "text": "a qué hora abren mañana"}'
            ']}'
        )
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(
                "dame una barracuda y a qué hora abren mañana",
                BIELA_CONTEXT, "David",
            )
        assert result.segments == [
            (router.DOMAIN_ORDER, "dame una barracuda"),
            (router.DOMAIN_CUSTOMER_SERVICE, "a qué hora abren mañana"),
        ]
        # Multi-segment: backward-compat `domain` is None.
        assert result.domain is None

    def test_three_segments_within_cap(self):
        raw = (
            '{"segments": ['
            '{"domain": "order", "text": "a"},'
            '{"domain": "customer_service", "text": "b"},'
            '{"domain": "chat", "text": "c"}'
            ']}'
        )
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert len(result.segments) == 3

    def test_excess_segments_truncated(self):
        items = ",".join(
            f'{{"domain": "order", "text": "item {i}"}}'
            for i in range(10)
        )
        raw = f'{{"segments": [{items}]}}'
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert len(result.segments) == router.MAX_SEGMENTS_PER_TURN

    def test_invalid_domain_in_segment_skipped(self):
        raw = (
            '{"segments": ['
            '{"domain": "nonsense", "text": "x"},'
            '{"domain": "order", "text": "dame barracuda"}'
            ']}'
        )
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments == [(router.DOMAIN_ORDER, "dame barracuda")]

    def test_empty_text_in_segment_skipped(self):
        raw = (
            '{"segments": ['
            '{"domain": "order", "text": ""},'
            '{"domain": "customer_service", "text": "a qué hora abren"}'
            ']}'
        )
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments == [(router.DOMAIN_CUSTOMER_SERVICE, "a qué hora abren")]


class TestRouterClassifierFailures:
    def test_empty_segments_list_returns_none_segments(self):
        mock_llm = _mock_llm_returning('{"segments": []}')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments is None
        assert result.domain is None

    def test_missing_segments_key(self):
        mock_llm = _mock_llm_returning('{"domain": "order"}')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments is None

    def test_unparseable_json(self):
        mock_llm = _mock_llm_returning("not json")
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments is None

    def test_llm_unavailable(self):
        with patch("app.orchestration.router._get_llm_classifier", return_value=None):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments is None

    def test_llm_exception_returns_none(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("boom")
        with patch("app.orchestration.router._get_llm_classifier", return_value=llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.segments is None

    def test_empty_message_skips_classifier(self):
        with patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.segments is None


class TestRouterMetadata:
    def test_passes_business_id_in_langsmith_metadata(self):
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "x"}]}'
        )
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            router.route("x", BIELA_CONTEXT, "David")
        _, kwargs = mock_llm.invoke.call_args
        metadata = kwargs["config"]["metadata"]
        assert metadata["business_id"] == "biela"
        assert kwargs["config"]["run_name"] == "router_classifier"


class TestRouterPromptHasProductPriceRule:
    """
    Regression: a previous addition (promo discovery → customer_service)
    pulled product-price questions like "una picada qué valor?" into CS
    by lexical similarity. CS planner has no intent for product prices,
    so it returned a generic chat fallback. Main branch routes these
    correctly to `order` (so the order agent's GET_PRODUCT can answer
    with the price + a "¿quieres ordenar?" nudge).

    The fix is a router-prompt rule. We assert the rule is present so
    a future prompt edit can't silently delete it.
    """

    def test_prompt_routes_named_product_price_to_order(self):
        prompt = router._ROUTER_SYSTEM_PROMPT
        # The product-price-of-a-named-item rule.
        assert "PRECIO/VALOR de un producto NOMBRADO" in prompt, (
            "Router prompt must classify 'qué precio tiene la X' as `order`, "
            "not customer_service"
        )
        # Concrete examples the LLM can pattern-match against.
        for example in (
            "una picada qué valor",
            "cuánto vale la barracuda",
            "qué precio tiene",
        ):
            assert example in prompt, f"Router prompt missing example: {example!r}"

    def test_prompt_disambiguates_promo_listing_from_product_price(self):
        """The CS promo-discovery rule must NOT swallow product prices."""
        prompt = router._ROUTER_SYSTEM_PROMPT
        # Discriminator: NO specific catalog product is named for CS.
        assert "no nombra ningún producto específico del catálogo" in prompt, (
            "Promo-discovery CS rule must scope itself to messages that "
            "don't name a specific product"
        )
        # Generic price questions (no product) stay on CS.
        assert "cuánto cuesta el domicilio" in prompt, (
            "Router must distinguish generic-price (CS) from "
            "named-product-price (order)"
        )


class TestRouterPromptHasOrderingOpenerRule:
    """
    Regression: "para un domicilio" used to be misclassified as
    customer_service (the lexical token "domicilio" matched the
    delivery-policy CS rule). It's actually an opening signal —
    the customer wants to order but hasn't named a product yet.
    Main routes it to `order` and the order agent's planner replies
    with an invitation; multi-agent broke this until the discriminator
    was added.
    """

    def test_prompt_routes_ordering_opener_to_order(self):
        prompt = router._ROUTER_SYSTEM_PROMPT
        assert "INTENCIÓN DE PEDIR sin nombrar producto" in prompt, (
            "Router prompt must classify 'para un domicilio' / 'quiero pedir' "
            "as `order`, not customer_service"
        )
        # Concrete examples the LLM can pattern-match against.
        for example in (
            "para un domicilio",
            "un domicilio por favor",
            "quiero pedir",
            "para hacer un pedido",
        ):
            assert example in prompt, f"Router prompt missing example: {example!r}"

    def test_prompt_disambiguates_opener_from_delivery_price_question(self):
        """The discriminator: bare opener vs. interrogative."""
        prompt = router._ROUTER_SYSTEM_PROMPT
        # The disambiguation rule must explicitly call out both shapes.
        assert "para un domicilio" in prompt
        assert "cuánto vale el domicilio" in prompt or "cuánto cobran de domicilio" in prompt, (
            "Router prompt must show that the question form 'cuánto vale "
            "el domicilio' goes to customer_service"
        )
