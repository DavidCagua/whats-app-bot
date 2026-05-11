"""
Unit tests for the image branch in media_job — handler dispatch logic.

The vision call, promo lookup, and send_message are all mocked. We're
testing the branching: extracted-as-promo + 1 match → confirmation;
2+ matches → ambiguity prompt; 0 matches → fallback to active list;
extractor returns None → friendly receipt.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.workers import media_job


class TestHandlePromoScreenshot:
    def _stub_send_text_reply(self):
        """Replace _send_text_reply with a list-recorder for assertions."""
        sent: list[tuple[str, str, str]] = []

        def fake_send(wa_id, text, business_id):
            sent.append((wa_id, text, business_id))
            return True

        return sent, fake_send

    def test_single_match_persists_listed_promos_and_confirms(self, monkeypatch):
        """Match → write last_listed_promos to the session, send a
        confirmation that primes the SELECT_LISTED_PROMO follow-up."""
        sent, fake_send = self._stub_send_text_reply()
        promo = {
            "id": "promo-123",
            "name": "2 Honey Burger con papas",
            "fixed_price": 30000,
            "discount_amount": None,
            "discount_pct": None,
        }

        saved_state: list[dict] = []

        def fake_save(wa_id, business_id, state_update):
            saved_state.append(state_update)

        monkeypatch.setattr(media_job, "_send_text_reply", fake_send)
        with patch(
            "app.services.promotion_service.find_promo_by_query",
            return_value=[promo],
        ), patch(
            "app.database.session_state_service.session_state_service.save",
            side_effect=fake_save,
        ):
            media_job._handle_promo_screenshot(
                wa_id="+573177000722",
                business_id="biela",
                extracted={
                    "candidate_name": "2 Honey Burger con papas",
                    "mentioned_products": [],
                },
            )

        assert len(sent) == 1
        _, body, _ = sent[0]
        assert "2 Honey Burger con papas" in body
        assert "$30.000" in body
        # Must end with the question-to-add-it framing so the customer's
        # "sí" is unambiguous → SELECT_LISTED_PROMO → handoff to order.
        assert "agregue" in body.lower() or "agrego" in body.lower()

        # last_listed_promos persisted into the same slot CS PROMOS_LIST uses
        # (agent_contexts.customer_service — the only sub-dict the
        # ConversationSession model actually merges; the prior shape
        # `customer_service_context` was a silent-drop bug).
        assert len(saved_state) == 1
        cs_ctx = saved_state[0]["agent_contexts"]["customer_service"]
        assert cs_ctx["last_listed_promos"] == [
            {"id": "promo-123", "name": "2 Honey Burger con papas"}
        ]

    def test_falls_back_to_mentioned_products_when_candidate_missing(self, monkeypatch):
        """If candidate_name is None but mentioned_products has tokens,
        the matcher gets called with the joined product names."""
        sent, fake_send = self._stub_send_text_reply()
        monkeypatch.setattr(media_job, "_send_text_reply", fake_send)

        find = MagicMock(side_effect=[[]])  # no result by candidate
        find2 = MagicMock(return_value=[{
            "id": "p1", "name": "Combo X",
            "fixed_price": 25000, "discount_amount": None, "discount_pct": None,
        }])
        # First call returns []; we want to confirm a SECOND call happens
        # with the joined products.
        calls: list[str] = []

        def fake_find(business_id, query, **kwargs):
            calls.append(query)
            if query == "":
                return []
            if "Honey" in query and "Papas" in query:
                return [{
                    "id": "p1", "name": "Combo X",
                    "fixed_price": 25000,
                    "discount_amount": None, "discount_pct": None,
                }]
            return []

        with patch(
            "app.services.promotion_service.find_promo_by_query",
            side_effect=fake_find,
        ), patch(
            "app.database.session_state_service.session_state_service.save",
        ):
            media_job._handle_promo_screenshot(
                wa_id="+573177000722",
                business_id="biela",
                extracted={
                    "candidate_name": None,
                    "mentioned_products": ["Honey Burger", "Papas"],
                },
            )

        # Only the products-fallback path queried (candidate skipped because None).
        assert calls == ["Honey Burger Papas"]
        assert len(sent) == 1
        assert "Combo X" in sent[0][1]

    def test_multiple_matches_renders_ambiguity_prompt(self, monkeypatch):
        sent, fake_send = self._stub_send_text_reply()
        monkeypatch.setattr(media_job, "_send_text_reply", fake_send)

        with patch(
            "app.services.promotion_service.find_promo_by_query",
            return_value=[
                {"id": "p1", "name": "Combo A", "fixed_price": 20000,
                 "discount_amount": None, "discount_pct": None},
                {"id": "p2", "name": "Combo B", "fixed_price": 25000,
                 "discount_amount": None, "discount_pct": None},
            ],
        ), patch(
            "app.database.session_state_service.session_state_service.save",
        ):
            media_job._handle_promo_screenshot(
                wa_id="+573177000722",
                business_id="biela",
                extracted={"candidate_name": "Combo", "mentioned_products": []},
            )

        assert len(sent) == 1
        body = sent[0][1]
        assert "Combo A" in body and "Combo B" in body
        assert "varias promos coinciden" in body.lower()

    def test_no_match_lists_active_promos_as_alternatives(self, monkeypatch):
        sent, fake_send = self._stub_send_text_reply()
        monkeypatch.setattr(media_job, "_send_text_reply", fake_send)

        with patch(
            "app.services.promotion_service.find_promo_by_query",
            return_value=[],
        ), patch(
            "app.services.promotion_service.list_active_promos",
            return_value=[
                {"id": "p1", "name": "Promo Lunes"},
                {"id": "p2", "name": "Promo Viernes"},
            ],
        ):
            media_job._handle_promo_screenshot(
                wa_id="+573177000722",
                business_id="biela",
                extracted={"candidate_name": "Combo Inexistente", "mentioned_products": []},
            )

        assert len(sent) == 1
        body = sent[0][1]
        assert "no la tenemos activa hoy" in body
        assert "Promo Lunes" in body and "Promo Viernes" in body

    def test_no_match_and_no_active_promos_falls_back_gracefully(self, monkeypatch):
        sent, fake_send = self._stub_send_text_reply()
        monkeypatch.setattr(media_job, "_send_text_reply", fake_send)

        with patch(
            "app.services.promotion_service.find_promo_by_query",
            return_value=[],
        ), patch(
            "app.services.promotion_service.list_active_promos",
            return_value=[],
        ):
            media_job._handle_promo_screenshot(
                wa_id="+573177000722",
                business_id="biela",
                extracted={"candidate_name": "X", "mentioned_products": []},
            )

        assert len(sent) == 1
        assert "no tenemos promos activas" in sent[0][1]


class TestHandleImageMessage:
    """Dispatcher decides between promo template, agent-on-caption, or receipt."""

    def _silence_proc_key(self, monkeypatch):
        """proc_key set/clear should be no-ops in unit tests (no Redis)."""
        monkeypatch.setattr(media_job, "_mark_processing_for_abort_key", lambda k: False)
        monkeypatch.setattr(media_job, "_clear_processing_for_abort_key", lambda k: None)

    def test_promo_screenshot_no_caption_uses_templated_handler(self, monkeypatch):
        self._silence_proc_key(monkeypatch)
        promo_called = MagicMock()
        receipt_called = MagicMock()
        monkeypatch.setattr(media_job, "_handle_promo_screenshot", promo_called)
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value={
                "is_promo_screenshot": True,
                "candidate_name": "X",
                "mentioned_products": [],
                "promo_text": "",
            },
        ):
            media_job._handle_image_message(conv, "https://example.test/img.jpg", "", None)

        promo_called.assert_called_once()
        receipt_called.assert_not_called()

    def test_promo_screenshot_with_caption_still_uses_promo_template(self, monkeypatch):
        """Caption + promo screenshot — promo template wins. The customer
        attached the image because they want the promo; the caption is
        a question about it ('la tiene?'), not a separate ordering intent."""
        self._silence_proc_key(monkeypatch)
        promo_called = MagicMock()
        receipt_called = MagicMock()
        agent_called = MagicMock()
        monkeypatch.setattr(media_job, "_handle_promo_screenshot", promo_called)
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value={
                "is_promo_screenshot": True,
                "candidate_name": "Honey Burger",
                "mentioned_products": [],
                "promo_text": "",
            },
        ), patch(
            "app.handlers.whatsapp_handler.run_agent_and_send_reply", agent_called,
        ):
            media_job._handle_image_message(
                conv, "https://example.test/img.jpg", "la tiene?", None,
            )

        promo_called.assert_called_once()
        receipt_called.assert_not_called()
        agent_called.assert_not_called()

    def test_non_promo_with_caption_runs_agent_on_caption(self, monkeypatch):
        """Non-promo image + caption ('qué es esto?') — vision can't help;
        run the agent on the caption text alone (same as text-only flow)."""
        self._silence_proc_key(monkeypatch)
        agent_called = MagicMock()
        promo_called = MagicMock()
        receipt_called = MagicMock()
        monkeypatch.setattr(media_job, "_handle_promo_screenshot", promo_called)
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value={
                "is_promo_screenshot": False,
                "candidate_name": None,
                "mentioned_products": [],
                "promo_text": "",
            },
        ), patch(
            "app.handlers.whatsapp_handler.run_agent_and_send_reply", agent_called,
        ), patch("app.create_app", return_value=MagicMock()):
            media_job._handle_image_message(
                conv, "https://example.test/img.jpg", "qué es esto?", None,
            )

        promo_called.assert_not_called()
        receipt_called.assert_not_called()
        agent_called.assert_called_once()
        kwargs = agent_called.call_args.kwargs
        assert kwargs["message_text"] == "qué es esto?"
        assert kwargs["wa_id"] == "+573177000722"
        assert kwargs["business_id"] == "biela"

    def test_non_promo_no_caption_falls_back_to_receipt(self, monkeypatch):
        self._silence_proc_key(monkeypatch)
        promo_called = MagicMock()
        receipt_called = MagicMock()
        agent_called = MagicMock()
        monkeypatch.setattr(media_job, "_handle_promo_screenshot", promo_called)
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value={
                "is_promo_screenshot": False,
                "candidate_name": None,
                "mentioned_products": [],
                "promo_text": "",
            },
        ), patch(
            "app.handlers.whatsapp_handler.run_agent_and_send_reply", agent_called,
        ):
            media_job._handle_image_message(conv, "https://example.test/img.jpg", "", None)

        promo_called.assert_not_called()
        agent_called.assert_not_called()
        receipt_called.assert_called_once_with("+573177000722", "biela")

    def test_extraction_failure_no_caption_still_sends_receipt(self, monkeypatch):
        """Extractor returned None (no API key / network error). With no
        caption to fall back on, send the friendly receipt — never silent."""
        self._silence_proc_key(monkeypatch)
        receipt_called = MagicMock()
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value=None,
        ):
            media_job._handle_image_message(conv, "https://example.test/img.jpg", "", None)

        receipt_called.assert_called_once()

    def test_extraction_failure_with_caption_runs_agent(self, monkeypatch):
        """Even if vision fails, a caption is real user intent — run the
        agent on it instead of dropping the customer's words."""
        self._silence_proc_key(monkeypatch)
        agent_called = MagicMock()
        receipt_called = MagicMock()
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value=None,
        ), patch(
            "app.handlers.whatsapp_handler.run_agent_and_send_reply", agent_called,
        ), patch("app.create_app", return_value=MagicMock()):
            media_job._handle_image_message(
                conv, "https://example.test/img.jpg", "una barracuda", None,
            )

        receipt_called.assert_not_called()
        agent_called.assert_called_once()
        assert agent_called.call_args.kwargs["message_text"] == "una barracuda"

    def test_skips_when_business_id_missing(self, monkeypatch):
        self._silence_proc_key(monkeypatch)
        promo_called = MagicMock()
        receipt_called = MagicMock()
        agent_called = MagicMock()
        monkeypatch.setattr(media_job, "_handle_promo_screenshot", promo_called)
        monkeypatch.setattr(media_job, "_send_image_receipt", receipt_called)

        conv = MagicMock(business_id=None, whatsapp_id="+573177000722")
        media_job._handle_image_message(conv, "https://example.test/img.jpg", "x", None)

        promo_called.assert_not_called()
        receipt_called.assert_not_called()
        agent_called.assert_not_called()


class TestProcKeyCoordination:
    """The image branch must set + clear proc_key so concurrent text
    messages from the same customer trigger ABORT + requeue (cross-modal
    coordination — same primitive used for text↔text races)."""

    def test_mark_processing_called_with_abort_key(self, monkeypatch):
        marks: list[str] = []
        clears: list[str] = []
        monkeypatch.setattr(
            media_job, "_mark_processing_for_abort_key",
            lambda k: marks.append(k) or True,
        )
        monkeypatch.setattr(
            media_job, "_clear_processing_for_abort_key",
            lambda k: clears.append(k),
        )

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            return_value=None,
        ), patch.object(media_job, "_send_image_receipt"):
            media_job._handle_image_message(
                conv, "https://example.test/img.jpg", "",
                "abort:whatsapp:+14155238886:+573177000722",
            )

        assert marks == ["abort:whatsapp:+14155238886:+573177000722"]
        assert clears == ["abort:whatsapp:+14155238886:+573177000722"]

    def test_clear_runs_even_if_handler_raises(self, monkeypatch):
        """proc_key must clear in finally so a hung turn doesn't strand
        the customer's next text behind an orphaned processing flag."""
        marks: list[str] = []
        clears: list[str] = []
        monkeypatch.setattr(
            media_job, "_mark_processing_for_abort_key",
            lambda k: marks.append(k) or True,
        )
        monkeypatch.setattr(
            media_job, "_clear_processing_for_abort_key",
            lambda k: clears.append(k),
        )

        conv = MagicMock(business_id="biela", whatsapp_id="+573177000722")
        with patch(
            "app.services.image_promo_extractor.extract_promo_from_image",
            side_effect=RuntimeError("vision API down"),
        ):
            with pytest.raises(RuntimeError):
                media_job._handle_image_message(
                    conv, "https://example.test/img.jpg", "",
                    "abort:whatsapp:+14155238886:+573177000722",
                )

        assert marks == ["abort:whatsapp:+14155238886:+573177000722"]
        # Cleared even though the inner call raised.
        assert clears == ["abort:whatsapp:+14155238886:+573177000722"]


class TestParseAbortKey:
    """The abort_key parser must tolerate Twilio's 'whatsapp:+E164' format
    (multiple colons) and return None on malformed input — best-effort,
    never raises."""

    def test_parses_twilio_abort_key(self):
        parsed = media_job._parse_abort_key("abort:whatsapp:+14155238886:+573177000722")
        assert parsed == ("whatsapp:+14155238886", "+573177000722")

    def test_parses_meta_abort_key(self):
        parsed = media_job._parse_abort_key("abort:14155238886:573177000722")
        assert parsed == ("14155238886", "573177000722")

    def test_returns_none_for_missing_prefix(self):
        assert media_job._parse_abort_key("processing:foo:+57") is None

    def test_returns_none_for_empty_or_none(self):
        assert media_job._parse_abort_key(None) is None
        assert media_job._parse_abort_key("") is None

    def test_returns_none_for_no_separator(self):
        assert media_job._parse_abort_key("abort:onlyonepart") is None


class TestMediaPlaceholdersTreatedAsNoCaption:
    """
    Regression: conversation_service.store_conversation_message_with_attachments
    substitutes empty captions with "[media]" / "[audio]" so the row
    never has a literally empty `message`. The media job's image branch
    previously treated those placeholders as a caption (truthy string)
    and skipped the vision pipeline. The fix recognizes them as
    sentinels — image-only messages route to the handler regardless.
    """

    def test_placeholders_set_includes_media_and_audio(self):
        """Pin the set so a future placeholder added in conversation_service
        either (a) extends this set or (b) breaks this test loudly."""
        assert "[media]" in media_job._MEDIA_PLACEHOLDERS
        assert "[audio]" in media_job._MEDIA_PLACEHOLDERS

    def test_placeholder_string_is_treated_as_no_caption(self):
        """The actual logic in process_media_job's image branch is
        `caption = "" if raw_message in _MEDIA_PLACEHOLDERS else raw_message`.
        Reproduce it and assert the contract."""
        for placeholder in ("[media]", "[audio]", "[image]"):
            raw = placeholder
            caption = "" if raw in media_job._MEDIA_PLACEHOLDERS else raw
            assert caption == "", (
                f"placeholder {placeholder!r} must reduce to empty caption, "
                f"otherwise image-only handler is silently skipped"
            )

    def test_real_caption_is_preserved(self):
        """A genuine caption like "tienen esta promo?" must NOT be reduced
        — the agent already handled it on the original turn."""
        raw = "tienen esta promo?"
        caption = "" if raw in media_job._MEDIA_PLACEHOLDERS else raw
        assert caption == "tienen esta promo?"
