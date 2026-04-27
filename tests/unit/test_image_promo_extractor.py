"""
Unit tests for image_promo_extractor — vision call response shaping.

The actual OpenAI vision call is mocked; these tests verify the wrapper
parses + sanitizes the model's JSON response and degrades gracefully
on every failure mode (no API key, no URL, parse error, network error).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import image_promo_extractor as ipe


def _mock_openai_returning(content: str):
    """Build a mock OpenAI client whose chat.completions.create returns
    a response with the given message content."""
    fake_choice = MagicMock()
    fake_choice.message.content = content
    fake_response = MagicMock()
    fake_response.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    return fake_client


class TestExtractPromoFromImage:
    def test_returns_none_for_empty_url(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        assert ipe.extract_promo_from_image("") is None
        assert ipe.extract_promo_from_image(None) is None  # type: ignore[arg-type]

    def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert ipe.extract_promo_from_image("https://example.test/img.jpg") is None

    def test_parses_valid_promo_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning(json.dumps({
            "is_promo_screenshot": True,
            "candidate_name": "2 Honey Burger con papas",
            "mentioned_products": ["Honey Burger", "Papas"],
            "promo_text": "2 HONEY BURGER + PAPAS — $30.000 — LUNES Y VIERNES",
        }))
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")

        assert result is not None
        assert result["is_promo_screenshot"] is True
        assert result["candidate_name"] == "2 Honey Burger con papas"
        assert result["mentioned_products"] == ["Honey Burger", "Papas"]
        assert "30.000" in result["promo_text"]

    def test_parses_non_promo_response(self, monkeypatch):
        """The model says it's not a promo (e.g. food photo, payment receipt)."""
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning(json.dumps({
            "is_promo_screenshot": False,
            "candidate_name": None,
            "mentioned_products": [],
            "promo_text": "",
        }))
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")

        assert result is not None
        assert result["is_promo_screenshot"] is False
        assert result["candidate_name"] is None
        assert result["mentioned_products"] == []

    def test_normalizes_empty_candidate_name_to_none(self, monkeypatch):
        """Whitespace-only candidate_name should round-trip as None so
        the matcher doesn't try to query with empty input."""
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning(json.dumps({
            "is_promo_screenshot": True,
            "candidate_name": "   ",
            "mentioned_products": ["Burger"],
            "promo_text": "BURGER 20K",
        }))
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")
        assert result["candidate_name"] is None

    def test_strips_whitespace_in_mentioned_products(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning(json.dumps({
            "is_promo_screenshot": True,
            "candidate_name": "Promo X",
            "mentioned_products": ["  Burger  ", "", "  ", "Papas"],
            "promo_text": "x",
        }))
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")
        # Empty / whitespace-only entries dropped; surviving ones stripped.
        assert result["mentioned_products"] == ["Burger", "Papas"]

    def test_returns_none_on_invalid_json(self, monkeypatch):
        """The model went off-script — wrapper must not crash."""
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning("not json at all")
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")
        assert result is None

    def test_returns_none_on_openai_exception(self, monkeypatch):
        """Network error / rate limit / invalid model — never raise."""
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("rate limited")
        with patch("openai.OpenAI", return_value=client):
            result = ipe.extract_promo_from_image("https://example.test/img.jpg")
        assert result is None

    def test_passes_image_url_in_user_message(self, monkeypatch):
        """The vision API expects the image as a multimodal content block.
        Pin the request shape so a future refactor doesn't accidentally
        drop the image and silently classify a text-only request."""
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        client = _mock_openai_returning(json.dumps({
            "is_promo_screenshot": False,
            "candidate_name": None,
            "mentioned_products": [],
            "promo_text": "",
        }))
        with patch("openai.OpenAI", return_value=client):
            ipe.extract_promo_from_image("https://example.test/img.jpg")

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-mini"
        # Find the user message with the multimodal content block.
        user_msg = next(m for m in call_kwargs["messages"] if m["role"] == "user")
        # The content must be a list (text + image_url blocks).
        assert isinstance(user_msg["content"], list)
        image_block = next(
            b for b in user_msg["content"] if b.get("type") == "image_url"
        )
        assert image_block["image_url"]["url"] == "https://example.test/img.jpg"
        # JSON mode requested.
        assert call_kwargs["response_format"] == {"type": "json_object"}
