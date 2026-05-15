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
        assert result.direct_reply.startswith("Hola David 👋 Bienvenido a Biela")


class TestRouterLLMGreetingDomain:
    """
    Compound greetings ("hola buenas noches", "buenas qué más") miss the
    regex fast-path on purpose — the LLM router catches them and returns
    the same `direct_reply` shape so conversation_manager dispatches the
    Twilio CTA welcome card via the same code path as the regex hit.
    """

    def test_lone_greeting_segment_converts_to_direct_reply(self):
        # Compound greeting that would miss the regex; LLM tags it as greeting.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "greeting", "text": "hola buenas noches"}]}'
        )
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("hola buenas noches", BIELA_CONTEXT, "David")
        # Same shape as the regex fast-path: direct_reply set, no segments.
        assert result.direct_reply is not None
        assert "Biela" in result.direct_reply
        assert result.segments is None
        assert result.domain is None

    def test_greeting_segment_dropped_when_mixed_with_substantive_segment(self):
        # Defense-in-depth: if the LLM ever emits greeting alongside another
        # domain (it shouldn't per prompt rules), drop greeting and let the
        # substantive segment dispatch.
        mock_llm = _mock_llm_returning(
            '{"segments": ['
            '{"domain": "greeting", "text": "hola"},'
            '{"domain": "order", "text": "una barracuda"}'
            ']}'
        )
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("hola dame una barracuda", BIELA_CONTEXT, "David")
        assert result.direct_reply is None
        assert result.segments == [(router.DOMAIN_ORDER, "una barracuda")]


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


class TestRouterDeterministicPriceOfProduct:
    """
    Regression: production observation 2026-05-03 (Biela / 3177000722) —
    "Cuánto vale el pegoretti?" was misrouted to customer_service →
    cs_chat_fallback. The LLM router prompt covers this case in theory,
    but the LLM ignored the rule when the product name was unfamiliar.
    The deterministic pre-classifier short-circuits the LLM: catalog match
    + price interrogative → `order`, no LLM call.
    """

    @pytest.fixture
    def biela_lookup_set(self):
        return frozenset({
            "pegoretti", "barracuda", "picada", "honey", "burger",
            "montesa", "queso", "mora", "jugos", "americana",
        })

    def _route_with_lookup(self, message, lookup_set, mock_llm=None):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=lookup_set,
        ):
            if mock_llm is not None:
                with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
                    return router.route(message, BIELA_CONTEXT, "David")
            with patch("app.orchestration.router._get_llm_classifier") as m:
                result = router.route(message, BIELA_CONTEXT, "David")
                return result, m

    @pytest.mark.parametrize(
        "msg",
        [
            "Cuánto vale el pegoretti?",
            "cuanto vale el pegoretti",
            "qué precio tiene la barracuda?",
            "una picada qué valor?",
            "cuánto cuesta la honey burger",
            "el precio de la montesa",
            "qué valor tiene la barracuda",
        ],
    )
    def test_named_product_price_short_circuits_to_order(self, msg, biela_lookup_set):
        result, llm_factory = self._route_with_lookup(msg, biela_lookup_set)
        assert result.segments == [(router.DOMAIN_ORDER, msg)]
        # Crucially: the LLM router was never built/called.
        llm_factory.assert_not_called()

    @pytest.mark.parametrize(
        "msg",
        [
            "cuánto cobran de domicilio",
            "cuánto vale el domicilio",
            "qué precio tiene el envío",
            "cuánto cuesta la propina",
        ],
    )
    def test_policy_price_questions_fall_through_to_llm(self, msg, biela_lookup_set):
        # No catalog token in the message — must NOT short-circuit; LLM router runs.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(msg, BIELA_CONTEXT, "David")
        # LLM ran (we routed to CS in the mock).
        mock_llm.invoke.assert_called_once()
        assert result.segments == [(router.DOMAIN_CUSTOMER_SERVICE, "x")]

    def test_named_product_without_price_word_falls_through(self, biela_lookup_set):
        # Bare product mention without an interrogative — let the LLM decide
        # (could be ADD_TO_CART, GET_PRODUCT details, etc.).
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "una pegoretti"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("una pegoretti", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.segments == [(router.DOMAIN_ORDER, "una pegoretti")]

    def test_price_word_without_catalog_match_falls_through(self, biela_lookup_set):
        # "cuánto vale" but the noun isn't in the lookup set — let the LLM decide.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("cuánto vale eso?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_empty_lookup_set_falls_through(self):
        # No catalog cached / new business — must NOT short-circuit on an
        # empty set (would let any "cuánto vale X" through to order).
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("cuánto vale el pegoretti?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_lookup_set_failure_falls_through(self):
        # If the lookup-set helper raises, the router must not crash —
        # it should fall through to the LLM classifier.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            side_effect=RuntimeError("boom"),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("cuánto vale el pegoretti?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_accent_insensitive_match(self, biela_lookup_set):
        # User writes "Pégorétti" — the normalizer must strip accents
        # before checking against the (already-normalized) lookup set.
        result, llm_factory = self._route_with_lookup(
            "Cuánto vale el Pégorétti?", biela_lookup_set,
        )
        assert result.segments == [(router.DOMAIN_ORDER, "Cuánto vale el Pégorétti?")]
        llm_factory.assert_not_called()

    def test_greeting_still_takes_priority(self, biela_lookup_set):
        # Greeting fast-path must still win over the deterministic check.
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("hola", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.direct_reply is not None
        assert result.segments is None


class TestRouterDeterministicPromoAdd:
    """
    Regression: production observation 2026-05-11 (Biela / 3177000722) —
    "una promo de oregon" was misrouted to customer_service → get_promos
    → "Si quieres alguna, dime cuál" (list-and-ask), instead of straight
    to order → add_promo_to_cart. The Spanish article-noun construction
    is imperative ("give me a promo of oregon") but the LLM saw the
    promo keyword without an explicit verb and defaulted to inquiry.

    Pre-classifier short-circuits the LLM: imperative trigger + promo
    keyword + identifier (no interrogative cue) → order.
    """

    @pytest.fixture
    def biela_lookup_set(self):
        return frozenset({"oregon", "honey", "burger", "barracuda"})

    def _route(self, message, lookup):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=lookup,
        ), patch("app.orchestration.router._get_llm_classifier") as llm_factory:
            return router.route(message, BIELA_CONTEXT, "David"), llm_factory

    @pytest.mark.parametrize(
        "msg",
        [
            "una promo de oregon",
            "un combo familiar",
            "una oferta del lunes",
            "dame una promo de honey",
            "quiero el combo lunes",
            "regalame una promo de oregon",
            "una promo del honey",
        ],
    )
    def test_imperative_promo_with_identifier_short_circuits_to_order(
        self, msg, biela_lookup_set,
    ):
        result, llm_factory = self._route(msg, biela_lookup_set)
        assert result.segments == [(router.DOMAIN_ORDER, msg)]
        # Crucially: the LLM router was never built/called.
        llm_factory.assert_not_called()

    @pytest.mark.parametrize(
        "msg",
        [
            # interrogative: clearly an info question
            "qué promos tienen?",
            "tienes alguna promo?",
            "tienen promo del lunes?",
            "hay alguna promo",
            "qué combos manejan",
            # no identifier: ambiguous, let the LLM decide
            "una promo",
            # no imperative head: fragment, let the LLM decide
            "promo de oregon",
            # question mark: even with imperative-looking form, it's a question
            "una promo de oregon?",
        ],
    )
    def test_inquiries_and_fragments_fall_through_to_llm(
        self, msg, biela_lookup_set,
    ):
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(msg, BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.segments == [(router.DOMAIN_CUSTOMER_SERVICE, "x")]


class TestRouterStuckArticleSplitter:
    """
    Regression: production observation 2026-05-05 (Biela / 3177000722) —
    "unabimota" (no space) was misrouted to customer_service →
    cs_chat_fallback. The LLM router saw a single unknown token and
    couldn't recover. The splitter rewrites stuck-article tokens
    against the catalog lookup-set and forces DOMAIN_ORDER.
    """

    @pytest.fixture
    def biela_lookup_set(self):
        return frozenset({
            "bimota", "barracuda", "picada", "honey", "burger",
            "montesa", "pegoretti", "ramona", "americana",
        })

    @pytest.mark.parametrize(
        "msg,expected_segment",
        [
            ("unabimota", "una bimota"),
            ("unaBimota", "una Bimota"),
            ("UNABIMOTA", "UNA BIMOTA"),
            ("elpegoretti", "el pegoretti"),
            ("lapicada", "la picada"),
            ("unabarracuda", "una barracuda"),
            ("unaramona", "una ramona"),
        ],
    )
    def test_stuck_article_token_routes_to_order_with_rewrite(
        self, msg, expected_segment, biela_lookup_set,
    ):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route(msg, BIELA_CONTEXT, "David")
            m.assert_not_called()
        # The LLM must NOT have been called.
        assert result.segments is not None
        assert len(result.segments) == 1
        domain, segment_text = result.segments[0]
        assert domain == router.DOMAIN_ORDER
        # Casing of the original article is preserved.
        assert expected_segment.lower() in segment_text.lower()
        assert " " in segment_text  # split actually inserted a space

    def test_stuck_article_with_punctuation_preserved(self, biela_lookup_set):
        # Trailing punctuation ("!", "?") must survive the rewrite.
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("unabimota!", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.segments[0][0] == router.DOMAIN_ORDER
        assert "!" in result.segments[0][1]

    def test_short_suffix_does_not_split(self, biela_lookup_set):
        # "elote" must NOT split into "el ote" (suffix too short, and
        # "ote" isn't in the lookup anyway). Falls through to the LLM.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("elote", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_no_stuck_article_falls_through(self, biela_lookup_set):
        # Plain message without a stuck-article token must NOT be
        # rewritten — LLM router runs as normal.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "una bimota"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("una bimota", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_stuck_article_with_unknown_suffix_falls_through(self, biela_lookup_set):
        # "unaXXXXX" where the suffix isn't in the catalog — splitter
        # must NOT fire, LLM runs normally.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("unaxxxxx", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_price_of_product_takes_priority_over_splitter(self, biela_lookup_set):
        # If both checks would fire, the existing price-of-product
        # check runs first — splitter is a separate hop. Verify the
        # message goes to order either way.
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("cuánto vale el pegoretti?", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.segments[0][0] == router.DOMAIN_ORDER

    def test_greeting_still_takes_priority_over_splitter(self, biela_lookup_set):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=biela_lookup_set,
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("hola", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.direct_reply is not None
        assert result.segments is None

    def test_empty_lookup_falls_through(self):
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("unabimota", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()

    def test_lookup_failure_falls_through(self):
        # If the lookup-set helper raises, the splitter must not crash —
        # router falls through to the LLM classifier.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            side_effect=RuntimeError("boom"),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("unabimota", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()


class TestRouterMultiWordProductNameShortCircuit:
    """
    Regression: 2026-05-06 (Biela / 3147554464). User said "Regálame una
    hamburguesa a la vuelta" (turn 1) and "Tienes la a la Vuelta?"
    (turn 3). LA VUELTA exists in the catalog, but the bot replied
    "Se ha agregado la HONEY BURGER (a la vuelta)" — planner picked
    HONEY BURGER from a recently-listed options block and dumped the
    real product name into notes.

    The router must detect multi-word catalog product names as
    contiguous substrings of the message, force ``order`` routing,
    AND surface a ``recognized_product`` hint that the order planner
    honors over its abbreviated-name rule.
    """

    @pytest.fixture
    def biela_full_names(self):
        return {
            "la vuelta": "LA VUELTA",
            "honey burger": "HONEY BURGER",
            "al pastor": "AL PASTOR",
            "mexican burger": "MEXICAN BURGER",
            "biela fries": "BIELA FRIES",
            "papas pergretti": "PAPAS PERGRETTI",
            "jugos en agua": "Jugos en agua",
            "jugos en leche": "Jugos en leche",
        }

    def _route(self, message, full_names):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset({"vuelta", "honey", "burger", "pastor"}),
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            return router.route(message, BIELA_CONTEXT, "David"), m

    @pytest.mark.parametrize(
        "msg,expected",
        [
            ("Regálame una hamburguesa a la vuelta", "LA VUELTA"),
            ("Tienes la a la Vuelta?", "LA VUELTA"),
            ("Quiero una honey burger", "HONEY BURGER"),
            ("una al pastor por favor", "AL PASTOR"),
            ("dame unas biela fries", "BIELA FRIES"),
            ("un jugos en leche de mora", "Jugos en leche"),
        ],
    )
    def test_multi_word_product_routes_to_order_with_recognized(self, msg, expected, biela_full_names):
        result, llm_factory = self._route(msg, biela_full_names)
        # LLM must NOT have been called.
        llm_factory.assert_not_called()
        assert result.segments is not None and len(result.segments) == 1
        assert result.segments[0][0] == router.DOMAIN_ORDER
        assert result.recognized_product == expected

    def test_no_multi_word_match_falls_through_to_llm(self, biela_full_names):
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("a qué hora abren?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_multiple_matches_punt_to_llm(self, biela_full_names):
        # When the message contains MULTIPLE multi-word product names,
        # we don't know which one the user meant — let the LLM decide.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(
                "una honey burger y un al pastor", BIELA_CONTEXT, "David",
            )
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_punctuation_does_not_block_match(self, biela_full_names):
        # Trailing "?" / leading "¿" must not prevent the match.
        result, llm_factory = self._route("¿Tienes la vuelta?", biela_full_names)
        llm_factory.assert_not_called()
        assert result.recognized_product == "LA VUELTA"

    def test_substring_must_be_token_aligned(self, biela_full_names):
        # "xxxla vueltayyy" — "la vuelta" appears inside but not as a
        # full token-aligned substring (no space before "la" — it's
        # preceded by "xxx"). The padding-space trick must reject this.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "chat", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("xxxla vueltayyy", BIELA_CONTEXT, "David")
        # No spurious recognition; the LLM ran (no short-circuit fired).
        assert result.recognized_product is None
        mock_llm.invoke.assert_called_once()

    def test_empty_full_name_map_falls_through(self):
        # Brand-new business with no products yet → empty map → no
        # recognition → LLM runs as today.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value={},
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("una hamburguesa a la vuelta", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_full_name_map_failure_falls_through(self):
        # If the full-name-map helper raises, the recognition must
        # not crash the router.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            side_effect=RuntimeError("boom"),
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("una hamburguesa a la vuelta", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_greeting_still_takes_priority(self, biela_full_names):
        # Greeting fast-path must still win over the full-name short-circuit.
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("hola", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.direct_reply is not None
        assert result.recognized_product is None


class TestExpandStuckArticlesUnit:
    """Unit tests for the pure rewrite helper."""

    def test_basic_split(self):
        out = router._expand_stuck_articles("unabimota", frozenset({"bimota"}))
        assert out == "una bimota"

    def test_preserves_casing_of_article(self):
        out = router._expand_stuck_articles("UnaBimota", frozenset({"bimota"}))
        assert out.lower() == "una bimota"
        # The "U" prefix stays uppercase.
        assert out[0] == "U"

    def test_no_match_returns_original(self):
        msg = "una bimota"
        assert router._expand_stuck_articles(msg, frozenset({"bimota"})) is msg

    def test_empty_lookup_returns_original(self):
        msg = "unabimota"
        assert router._expand_stuck_articles(msg, frozenset()) is msg

    def test_preserves_trailing_punctuation(self):
        out = router._expand_stuck_articles("unabimota?", frozenset({"bimota"}))
        assert "?" in out
        assert "una" in out.lower()
        assert "bimota" in out.lower()

    def test_only_one_token_in_multi_word_message(self):
        # Only the stuck token gets rewritten; the rest stays intact.
        out = router._expand_stuck_articles(
            "hola unabimota gracias",
            frozenset({"bimota"}),
        )
        assert out.lower().count("bimota") == 1
        assert "hola" in out
        assert "gracias" in out

    def test_short_suffix_not_split(self):
        # "elote" — suffix "ote" is too short, must not split.
        out = router._expand_stuck_articles("elote", frozenset({"ote"}))
        assert out == "elote"


class TestSingleTokenProductRouting:
    """Regression — single-word catalog products short-circuit to order
    even when prefixed by a greeting.

    Production 2026-05-06 (Biela / 14155238886): user said "Buenas tiene
    la barracuda?" cold (no prior turn) and the bot replied with the CS
    chat fallback "No entendí bien tu pregunta". Root cause: BARRACUDA
    is a single-word product, the multi-word recognizer skipped it, the
    price-of-product short-circuit needs a price keyword, and the LLM
    classifier biased toward customer_service because of the leading
    "Buenas". The single-token map closes that gap deterministically.
    """

    @pytest.fixture
    def biela_full_names(self):
        return {
            "la vuelta": "LA VUELTA",
            "honey burger": "HONEY BURGER",
        }

    @pytest.fixture
    def biela_single_tokens(self):
        return {
            "barracuda": "BARRACUDA",
            "montesa": "MONTESA",
            "bimota": "BIMOTA",
            "beta": "BETA",
        }

    def _route(self, message, full_names, single_tokens, lookup=None):
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_single_token_map",
            return_value=single_tokens,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=lookup or frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier") as m:
            return router.route(message, BIELA_CONTEXT, "David"), m

    @pytest.mark.parametrize(
        "msg,expected",
        [
            ("Buenas tiene la barracuda?", "BARRACUDA"),
            ("Buenas tardes tienen montesa?", "MONTESA"),
            ("Hola tienen bimota?", "BIMOTA"),
            ("tiene la barracuda?", "BARRACUDA"),
            ("tienen barracuda", "BARRACUDA"),
            # Casing / accent shouldn't matter.
            ("BUENAS TIENE LA BARRACUDA?", "BARRACUDA"),
        ],
    )
    def test_greeting_plus_single_word_product_routes_to_order(
        self, msg, expected, biela_full_names, biela_single_tokens,
    ):
        result, llm_factory = self._route(msg, biela_full_names, biela_single_tokens)
        # LLM must NOT have been called — the deterministic short-circuit fired.
        llm_factory.assert_not_called()
        assert result.segments is not None and len(result.segments) == 1
        assert result.segments[0][0] == router.DOMAIN_ORDER
        assert result.recognized_product == expected

    def test_pure_greeting_still_takes_priority(self, biela_full_names, biela_single_tokens):
        # "hola" alone hits the greeting fast-path BEFORE any product
        # recognizer. Single-token map exists but is irrelevant here.
        result, llm_factory = self._route("hola", biela_full_names, biela_single_tokens)
        llm_factory.assert_not_called()
        assert result.direct_reply is not None
        assert result.recognized_product is None

    def test_no_product_token_falls_through_to_llm(self, biela_full_names, biela_single_tokens):
        # "Buenas a qué hora abren?" — greeting + CS question, no
        # catalog token. Must go to the LLM, not silently route to order.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_single_token_map",
            return_value=biela_single_tokens,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("Buenas a qué hora abren?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_two_single_tokens_in_message_punts_to_llm(
        self, biela_full_names, biela_single_tokens,
    ):
        # "una barracuda y una montesa" mentions two distinct catalog
        # products. Same disambiguation pattern as the multi-word path:
        # if more than one matches, let the LLM split into segments.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "order", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_single_token_map",
            return_value=biela_single_tokens,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(
                "una barracuda y una montesa", BIELA_CONTEXT, "David",
            )
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None

    def test_multi_word_match_takes_priority_over_single_token(
        self, biela_full_names, biela_single_tokens,
    ):
        # "una honey burger" matches the multi-word "HONEY BURGER" first;
        # we must not also try to single-token-match "burger" or "honey".
        # The multi-word path returns LA VUELTA / HONEY BURGER / etc.
        result, llm_factory = self._route(
            "una honey burger", biela_full_names, biela_single_tokens,
        )
        llm_factory.assert_not_called()
        assert result.recognized_product == "HONEY BURGER"

    def test_single_token_map_failure_falls_through(self, biela_full_names):
        # Cache helper raising must not crash routing — recognizer
        # returns None and the LLM runs as before.
        mock_llm = _mock_llm_returning(
            '{"segments": [{"domain": "customer_service", "text": "x"}]}'
        )
        with patch(
            "app.orchestration.router.catalog_cache.get_router_full_name_map",
            return_value=biela_full_names,
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_single_token_map",
            side_effect=RuntimeError("boom"),
        ), patch(
            "app.orchestration.router.catalog_cache.get_router_lookup_set",
            return_value=frozenset(),
        ), patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("tiene barracuda?", BIELA_CONTEXT, "David")
        mock_llm.invoke.assert_called_once()
        assert result.recognized_product is None


class TestSingleTokenMapBuilder:
    """Unit tests for catalog_cache._build_router_single_token_map.

    Documents the exclusion rules so a future refactor of the lookup
    rules doesn't silently re-introduce bad matches (multi-word names
    being collapsed, ambiguous duplicates leaking through, common
    short Spanish words showing up in the map).
    """

    @staticmethod
    def _build(products):
        from app.services import catalog_cache as _cc
        with patch.object(_cc, "list_products", return_value=products):
            return _cc._build_router_single_token_map("biz-1")

    def test_includes_single_word_active_products(self):
        products = [
            {"name": "BARRACUDA", "is_active": True},
            {"name": "MONTESA", "is_active": True},
        ]
        out = self._build(products)
        assert out == {"barracuda": "BARRACUDA", "montesa": "MONTESA"}

    def test_excludes_multi_word_products(self):
        # Multi-word names are owned by the full-name map. Including
        # them here would route "honey burger" via single-token match
        # on either "honey" or "burger" alone — wrong.
        products = [
            {"name": "HONEY BURGER", "is_active": True},
            {"name": "BIELA FRIES", "is_active": True},
        ]
        out = self._build(products)
        assert out == {}

    def test_excludes_short_tokens(self):
        # "ron" is a real product but only 3 chars — too risky against
        # common short Spanish words.
        products = [{"name": "RON", "is_active": True}]
        out = self._build(products)
        assert out == {}

    def test_drops_ambiguous_duplicate_token(self):
        # Two products normalize to the same single token ("AGUA"
        # bottled vs "AGUA" tap, hypothetically). The router can't
        # tell which one the user meant — drop both.
        products = [
            {"name": "AGUA", "is_active": True, "id": "p1"},
            {"name": "Agua", "is_active": True, "id": "p2"},
        ]
        # Two different canonical strings ("AGUA" vs "Agua") for the
        # same token "agua" — must be dropped from the map.
        out = self._build(products)
        assert "agua" not in out

    def test_normalizes_to_lowercase_token(self):
        products = [{"name": "Barracuda", "is_active": True}]
        out = self._build(products)
        assert out == {"barracuda": "Barracuda"}
