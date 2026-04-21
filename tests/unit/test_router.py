"""Unit tests for app/orchestration/router.py — greeting fast-path + LLM classifier."""

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


class TestRouterGreetingFastPath:
    @pytest.mark.parametrize("msg", ["hola", "buenas", "buenos días", "hey"])
    def test_pure_greeting_returns_direct_reply(self, msg):
        # LLM must NOT be called — greeting fast-path short-circuits.
        with patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route(
                message_body=msg,
                business_context=BIELA_CONTEXT,
                customer_name="David",
            )
            m.assert_not_called()
        assert result.direct_reply is not None
        assert "Biela" in result.direct_reply
        assert result.domain is None

    def test_greeting_includes_customer_name(self):
        result = router.route(
            message_body="hola",
            business_context=BIELA_CONTEXT,
            customer_name="David",
        )
        assert result.direct_reply.startswith("Hola David.")


def _mock_llm_returning(content: str):
    """Build a mock llm.invoke() that returns a LangChain-style response with .content."""
    llm = MagicMock()
    response = MagicMock()
    response.content = content
    llm.invoke.return_value = response
    return llm


class TestRouterLLMClassifier:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('{"domain": "order"}', router.DOMAIN_ORDER),
            ('{"domain": "customer_service"}', router.DOMAIN_CUSTOMER_SERVICE),
            ('{"domain": "catalog"}', router.DOMAIN_CATALOG),
            ('{"domain": "chat"}', router.DOMAIN_CHAT),
        ],
    )
    def test_parses_valid_domain_from_json(self, raw, expected):
        mock_llm = _mock_llm_returning(raw)
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route(
                message_body="quiero una barracuda",
                business_context=BIELA_CONTEXT,
                customer_name="David",
            )
        assert result.domain == expected
        assert result.direct_reply is None

    def test_strips_markdown_fences_from_response(self):
        mock_llm = _mock_llm_returning('```json\n{"domain": "order"}\n```')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain == router.DOMAIN_ORDER

    def test_extracts_json_from_wrapping_text(self):
        mock_llm = _mock_llm_returning('Respuesta: {"domain": "catalog"} listo.')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain == router.DOMAIN_CATALOG

    def test_invalid_domain_returns_none(self):
        mock_llm = _mock_llm_returning('{"domain": "nonsense"}')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain is None
        assert result.direct_reply is None

    def test_unparseable_response_returns_none(self):
        mock_llm = _mock_llm_returning("not json at all")
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain is None

    def test_llm_unavailable_returns_none(self):
        with patch("app.orchestration.router._get_llm_classifier", return_value=None):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain is None
        assert result.direct_reply is None

    def test_llm_exception_returns_none_no_crash(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("boom")
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            result = router.route("x", BIELA_CONTEXT, "David")
        assert result.domain is None

    def test_empty_message_skips_classifier(self):
        with patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.domain is None
        assert result.direct_reply is None

    def test_whitespace_only_message_skips_classifier(self):
        with patch("app.orchestration.router._get_llm_classifier") as m:
            result = router.route("   \n ", BIELA_CONTEXT, "David")
            m.assert_not_called()
        assert result.domain is None

    def test_passes_business_id_in_metadata(self):
        """LangSmith metadata should include business_id for filtering."""
        mock_llm = _mock_llm_returning('{"domain": "order"}')
        with patch("app.orchestration.router._get_llm_classifier", return_value=mock_llm):
            router.route("x", BIELA_CONTEXT, "David")
        _, kwargs = mock_llm.invoke.call_args
        metadata = kwargs["config"]["metadata"]
        assert metadata["business_id"] == "biela"
        assert kwargs["config"]["run_name"] == "router_classifier"
