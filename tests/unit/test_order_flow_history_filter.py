"""
Unit tests for context-aware disambiguation filtering.

When the user abbreviates a product name (e.g. "un special") after the
bot just listed products (e.g. SPECIAL DOG), the executor should prefer
the candidate that appeared in the recent listing over other catalog
matches (e.g. SPECIAL FRIES).

Tests cover:
1. _extract_product_names_from_history: regex extraction from bot messages
2. _filter_ambiguous_by_history: filtering ambiguous candidates by context
"""

import pytest

from app.orchestration.order_flow import (
    _extract_product_names_from_history,
    _filter_ambiguous_by_history,
)


# ---------------------------------------------------------------------------
# _extract_product_names_from_history
# ---------------------------------------------------------------------------

class TestExtractProductNamesFromHistory:
    def test_extracts_from_price_pattern(self):
        """Standard listing: 'PRODUCT ($XX.000)'."""
        history = [
            {"role": "assistant", "content": (
                "Tenemos:\n"
                "• SPECIAL DOG ($27.000)\n"
                "• DENVER ($27.000)\n"
                "• PEGORETTI ($27.000)\n"
            )},
        ]
        names = _extract_product_names_from_history(history)
        assert "special dog" in names
        assert "denver" in names
        assert "pegoretti" in names

    def test_extracts_from_bold_bullet_pattern(self):
        """Bold markdown listing: '• **PRODUCT** ($XX.000)'."""
        history = [
            {"role": "assistant", "content": (
                "Tenemos hamburguesas de pollo:\n"
                "• **ARIZONA** ($28.000) — Filete de pollo apanado.\n"
                "• **VITTORIA** ($28.000) — Filete de pollo, albahaca.\n"
            )},
        ]
        names = _extract_product_names_from_history(history)
        assert "arizona" in names
        assert "vittoria" in names

    def test_ignores_user_messages(self):
        """Only assistant messages should be scanned."""
        history = [
            {"role": "user", "content": "SPECIAL DOG ($27.000)"},
            {"role": "assistant", "content": "¿Qué te gustaría ordenar?"},
        ]
        names = _extract_product_names_from_history(history)
        assert "special dog" not in names

    def test_empty_history(self):
        assert _extract_product_names_from_history(None) == set()
        assert _extract_product_names_from_history([]) == set()

    def test_no_product_listings(self):
        history = [
            {"role": "assistant", "content": "Hola, ¿cómo te puedo ayudar?"},
        ]
        names = _extract_product_names_from_history(history)
        assert len(names) == 0

    def test_uses_last_4_assistant_messages(self):
        """Should look at last 4 assistant messages, not just the most recent."""
        history = [
            {"role": "assistant", "content": "SPECIAL DOG ($27.000)"},
            {"role": "user", "content": "hmm"},
            {"role": "assistant", "content": "BIELA FRIES ($28.000)"},
            {"role": "user", "content": "hmm"},
            {"role": "assistant", "content": "CORONA ($12.000)"},
            {"role": "user", "content": "hmm"},
            {"role": "assistant", "content": "BARRACUDA ($28.000)"},
        ]
        names = _extract_product_names_from_history(history)
        assert "special dog" in names
        assert "biela fries" in names
        assert "corona" in names
        assert "barracuda" in names


# ---------------------------------------------------------------------------
# _filter_ambiguous_by_history
# ---------------------------------------------------------------------------

class TestFilterAmbiguousByHistory:
    @staticmethod
    def _product(name):
        return {"id": f"p-{name.lower()}", "name": name, "price": 27000}

    def test_single_candidate_in_history_wins(self):
        """SPECIAL DOG was listed, SPECIAL FRIES was not → SPECIAL DOG wins."""
        matches = [
            self._product("SPECIAL DOG"),
            self._product("SPECIAL FRIES"),
        ]
        history = [
            {"role": "assistant", "content": (
                "Tenemos:\n"
                "• SPECIAL DOG ($27.000)\n"
                "• DENVER ($27.000)\n"
            )},
        ]
        winner = _filter_ambiguous_by_history(matches, history)
        assert winner is not None
        assert winner["name"] == "SPECIAL DOG"

    def test_both_candidates_in_history_returns_none(self):
        """If both candidates were listed, can't resolve — return None."""
        matches = [
            self._product("SPECIAL DOG"),
            self._product("SPECIAL FRIES"),
        ]
        history = [
            {"role": "assistant", "content": (
                "Tenemos:\n"
                "• SPECIAL DOG ($27.000)\n"
                "• SPECIAL FRIES ($30.000)\n"
            )},
        ]
        winner = _filter_ambiguous_by_history(matches, history)
        assert winner is None

    def test_no_candidates_in_history_returns_none(self):
        """If neither candidate was listed, can't resolve — return None."""
        matches = [
            self._product("SPECIAL DOG"),
            self._product("SPECIAL FRIES"),
        ]
        history = [
            {"role": "assistant", "content": "Hola, ¿qué deseas?"},
        ]
        winner = _filter_ambiguous_by_history(matches, history)
        assert winner is None

    def test_no_history_returns_none(self):
        matches = [self._product("SPECIAL DOG")]
        assert _filter_ambiguous_by_history(matches, None) is None
        assert _filter_ambiguous_by_history(matches, []) is None
