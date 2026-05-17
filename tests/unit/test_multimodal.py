"""
Tests for the multimodal content builder.

The builder turns the agent's ``attachments`` payload into OpenAI
chat-completions content parts. Images go through as ``image_url``
URLs; PDF documents are fetched and inlined as ``file_data`` base64
data URLs because the chat completions API doesn't accept PDF URLs
directly. Non-PDF documents (DOCX, XLSX, etc.) are skipped — the
model still sees the text.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from app.orchestration import multimodal


# ---------------------------------------------------------------------------
# Text-only / image paths (existing behavior — no regression).
# ---------------------------------------------------------------------------


class TestNoAttachments:
    def test_returns_plain_string(self):
        assert multimodal.build_user_content("hola", None) == "hola"

    def test_empty_list_returns_plain_string(self):
        assert multimodal.build_user_content("hola", []) == "hola"


class TestImageAttachments:
    def test_image_emits_image_url_part(self):
        out = multimodal.build_user_content(
            "mira", [{"type": "image", "url": "https://example.com/a.jpg"}],
        )
        assert isinstance(out, list)
        assert out[0] == {"type": "text", "text": "mira"}
        assert out[1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.com/a.jpg"},
        }

    def test_image_without_url_is_skipped(self):
        out = multimodal.build_user_content(
            "mira", [{"type": "image"}],
        )
        # No image part to emit → falls back to plain text.
        assert out == "mira"


# ---------------------------------------------------------------------------
# PDF document path (new).
# ---------------------------------------------------------------------------


def _mock_response(body: bytes, status: int = 200) -> MagicMock:
    """Build a requests.Response-like mock for streaming reads."""
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.iter_content = MagicMock(return_value=[body])
    return resp


class TestPdfDocumentAttachments:
    def test_pdf_is_fetched_and_inlined_as_file_data(self):
        pdf_bytes = b"%PDF-1.4\nfake content\n%%EOF\n"
        with patch(
            "app.orchestration.multimodal.requests.get",
            return_value=_mock_response(pdf_bytes),
        ):
            out = multimodal.build_user_content(
                "mira mi comprobante",
                [{
                    "type": "document",
                    "url": "https://example.com/x.pdf",
                    "content_type": "application/pdf",
                    "filename": "recibo.pdf",
                }],
            )
        assert isinstance(out, list), f"expected list of content parts, got {out!r}"
        assert out[0] == {"type": "text", "text": "mira mi comprobante"}
        file_part = out[1]
        assert file_part["type"] == "file"
        assert file_part["file"]["filename"] == "recibo.pdf"
        encoded = base64.b64encode(pdf_bytes).decode("ascii")
        assert file_part["file"]["file_data"] == (
            f"data:application/pdf;base64,{encoded}"
        )

    def test_pdf_defaults_filename_when_not_provided(self):
        with patch(
            "app.orchestration.multimodal.requests.get",
            return_value=_mock_response(b"%PDF-1.4\n"),
        ):
            out = multimodal.build_user_content(
                "x",
                [{
                    "type": "document",
                    "url": "https://example.com/x.pdf",
                    "content_type": "application/pdf",
                }],
            )
        assert out[1]["file"]["filename"] == "documento.pdf"

    def test_pdf_with_uppercase_mime_still_handled(self):
        with patch(
            "app.orchestration.multimodal.requests.get",
            return_value=_mock_response(b"%PDF-1.4\n"),
        ):
            out = multimodal.build_user_content(
                "x",
                [{
                    "type": "document",
                    "url": "https://example.com/x.pdf",
                    "content_type": "Application/PDF",
                }],
            )
        assert isinstance(out, list)
        assert out[1]["type"] == "file"

    def test_non_pdf_document_is_skipped(self):
        # DOCX, XLSX, etc. — chat completions API can't ingest them.
        # The builder must drop the attachment and return plain text so
        # the model still answers the text portion.
        with patch(
            "app.orchestration.multimodal.requests.get",
        ) as fake_get:
            out = multimodal.build_user_content(
                "te envío el archivo",
                [{
                    "type": "document",
                    "url": "https://example.com/x.docx",
                    "content_type":
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }],
            )
            # Builder short-circuits before any network call.
            fake_get.assert_not_called()
        assert out == "te envío el archivo"

    def test_pdf_fetch_failure_falls_back_to_text(self):
        with patch(
            "app.orchestration.multimodal.requests.get",
            side_effect=RuntimeError("network down"),
        ):
            out = multimodal.build_user_content(
                "ahí va",
                [{
                    "type": "document",
                    "url": "https://example.com/x.pdf",
                    "content_type": "application/pdf",
                }],
            )
        # Fetch failed → no file part → plain text fallback so the
        # planner still has something to respond to.
        assert out == "ahí va"

    def test_pdf_exceeding_size_cap_is_skipped(self):
        # Force the iter_content path to emit bytes past the cap.
        huge = b"a" * (multimodal._PDF_MAX_BYTES + 1)
        with patch(
            "app.orchestration.multimodal.requests.get",
            return_value=_mock_response(huge),
        ):
            out = multimodal.build_user_content(
                "grande",
                [{
                    "type": "document",
                    "url": "https://example.com/big.pdf",
                    "content_type": "application/pdf",
                }],
            )
        assert out == "grande"


class TestMixedAttachments:
    def test_image_plus_pdf_both_emitted(self):
        with patch(
            "app.orchestration.multimodal.requests.get",
            return_value=_mock_response(b"%PDF-1.4\n"),
        ):
            out = multimodal.build_user_content(
                "dos adjuntos",
                [
                    {"type": "image", "url": "https://example.com/a.jpg"},
                    {
                        "type": "document",
                        "url": "https://example.com/b.pdf",
                        "content_type": "application/pdf",
                    },
                ],
            )
        assert isinstance(out, list)
        # Order: text first, then media parts in attachment order.
        assert out[0]["type"] == "text"
        assert out[1]["type"] == "image_url"
        assert out[2]["type"] == "file"
