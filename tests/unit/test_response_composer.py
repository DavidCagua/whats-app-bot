"""Unit tests for app/orchestration/response_composer.py."""

from unittest.mock import patch, MagicMock

import pytest

from app.orchestration import response_composer


def _llm_returning(text: str):
    llm = MagicMock()
    resp = MagicMock()
    resp.content = text
    llm.invoke.return_value = resp
    return llm


class TestComposeEdgeCases:
    def test_empty_list_returns_empty_string(self):
        assert response_composer.compose([]) == ""

    def test_all_empty_messages_returns_empty_string(self):
        assert response_composer.compose(["", "   ", None]) == ""

    def test_single_message_returns_verbatim_without_llm_call(self):
        with patch("app.orchestration.response_composer._get_llm") as m:
            result = response_composer.compose(["Solo uno"])
            m.assert_not_called()
        assert result == "Solo uno"

    def test_single_with_empties_returns_the_one_non_empty(self):
        with patch("app.orchestration.response_composer._get_llm") as m:
            result = response_composer.compose(["", "Solo uno", ""])
            m.assert_not_called()
        assert result == "Solo uno"


class TestComposeMultipleMessages:
    def test_calls_llm_for_two_messages(self):
        llm = _llm_returning("Listo, agregué la barracuda. Y mañana abrimos a las 5:30 PM.")
        with patch("app.orchestration.response_composer._get_llm", return_value=llm):
            result = response_composer.compose([
                "Agregué una barracuda.",
                "Mañana abrimos a las 5:30 PM.",
            ])
        assert result == "Listo, agregué la barracuda. Y mañana abrimos a las 5:30 PM."
        llm.invoke.assert_called_once()

    def test_passes_numbered_responses_to_llm(self):
        llm = _llm_returning("merged")
        with patch("app.orchestration.response_composer._get_llm", return_value=llm):
            response_composer.compose(["A", "B", "C"])
        # Inspect what was sent
        args = llm.invoke.call_args[0][0]
        human_msg = args[1]
        assert "Respuesta 1:\nA" in human_msg.content
        assert "Respuesta 2:\nB" in human_msg.content
        assert "Respuesta 3:\nC" in human_msg.content

    def test_uses_run_name_response_composer(self):
        llm = _llm_returning("merged")
        with patch("app.orchestration.response_composer._get_llm", return_value=llm):
            response_composer.compose(["A", "B"])
        _, kwargs = llm.invoke.call_args
        assert kwargs["config"]["run_name"] == "response_composer"


class TestComposeFallback:
    def test_no_llm_available_falls_back_to_newline_concat(self):
        with patch("app.orchestration.response_composer._get_llm", return_value=None):
            result = response_composer.compose(["Primera.", "Segunda."])
        assert result == "Primera.\n\nSegunda."

    def test_llm_crash_falls_back_to_concat(self):
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("boom")
        with patch("app.orchestration.response_composer._get_llm", return_value=llm):
            result = response_composer.compose(["Primera.", "Segunda."])
        assert result == "Primera.\n\nSegunda."

    def test_llm_empty_response_falls_back_to_concat(self):
        llm = _llm_returning("")
        with patch("app.orchestration.response_composer._get_llm", return_value=llm):
            result = response_composer.compose(["Primera.", "Segunda."])
        assert result == "Primera.\n\nSegunda."
