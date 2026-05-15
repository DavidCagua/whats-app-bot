"""
Unit tests for whatsapp_utils helpers (pure functions, no network).
"""

from app.utils.whatsapp_utils import _split_for_twilio


class TestSplitForTwilio:
    """Regression for Twilio 21617 (1600-char limit) — long responses must be chunked."""

    def test_short_text_returns_single_chunk(self):
        assert _split_for_twilio("hola") == ["hola"]

    def test_empty_returns_single_empty(self):
        assert _split_for_twilio("") == [""]

    def test_respects_limit(self):
        text = "a" * 3500
        chunks = _split_for_twilio(text, limit=1500)
        assert len(chunks) >= 3
        assert all(len(c) <= 1500 for c in chunks)
        assert "".join(chunks) == text

    def test_splits_at_blank_line_boundary(self):
        # Two "paragraphs" separated by a blank line, each under limit but together over.
        para_a = "Hola. " * 150  # ~900 chars
        para_b = "Chao. " * 150  # ~900 chars
        text = para_a.strip() + "\n\n" + para_b.strip()
        chunks = _split_for_twilio(text, limit=1200)
        assert len(chunks) == 2
        assert chunks[0].startswith("Hola.")
        assert chunks[1].startswith("Chao.")

    def test_splits_at_newline_when_no_blank_line(self):
        lines = [f"Línea {i}: " + ("x" * 100) for i in range(20)]
        text = "\n".join(lines)
        chunks = _split_for_twilio(text, limit=1000)
        assert all(len(c) <= 1000 for c in chunks)
        # No chunk should start mid-line
        for c in chunks:
            assert c.lstrip().startswith("Línea")

    def test_splits_at_sentence_boundary(self):
        text = ("Esta es una oración muy larga número uno. " * 30).strip()
        chunks = _split_for_twilio(text, limit=500)
        assert len(chunks) >= 2
        assert all(len(c) <= 500 for c in chunks)
        # Each chunk should end with a period (sentence boundary) or be the last
        for c in chunks[:-1]:
            assert c.endswith(".")

    def test_falls_back_to_hard_split_when_no_spaces(self):
        text = "x" * 3000
        chunks = _split_for_twilio(text, limit=1000)
        assert all(len(c) <= 1000 for c in chunks)
        assert "".join(chunks) == text

    def test_bebidas_menu_scenario(self):
        """Realistic scenario: a list of 10 drinks with descriptions, totaling ~1800 chars."""
        items = []
        for i in range(10):
            items.append(
                f"- BEBIDA {i+1} ($5.000) — Descripción detallada con ingredientes, "
                f"tamaño y notas de preparación que ocupa bastante espacio por línea."
            )
        text = "Aquí tienes nuestras bebidas:\n\n" + "\n".join(items) + "\n\n¿Cuál te antoja?"
        chunks = _split_for_twilio(text, limit=800)
        assert all(len(c) <= 800 for c in chunks)
        # Must be at least 2 chunks since the original is over 800
        assert len(chunks) >= 2
        # Rejoining should recover all content (modulo whitespace at boundaries)
        rejoined = " ".join(chunks)
        for i in range(10):
            assert f"BEBIDA {i+1}" in rejoined
