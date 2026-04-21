"""Unit tests for app/orchestration/router.py — greeting fast-path only (Phase 1a)."""

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
        result = router.route(
            message_body=msg,
            business_context=BIELA_CONTEXT,
            customer_name="David",
        )
        assert result.direct_reply is not None
        assert "Biela" in result.direct_reply

    def test_greeting_includes_customer_name(self):
        result = router.route(
            message_body="hola",
            business_context=BIELA_CONTEXT,
            customer_name="David",
        )
        assert result.direct_reply.startswith("Hola David.")

    @pytest.mark.parametrize(
        "msg",
        [
            "hola quiero una barracuda",
            "a qué hora abren",
            "dame una coca",
            "hola cómo estás?",
            "",
            "   ",
        ],
    )
    def test_non_greeting_falls_through_to_agent_pipeline(self, msg):
        result = router.route(
            message_body=msg,
            business_context=BIELA_CONTEXT,
            customer_name="David",
        )
        assert result.direct_reply is None
