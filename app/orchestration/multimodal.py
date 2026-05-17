"""
Helper for building OpenAI-compatible multimodal user-message content.

The router and the CS / order agents all need to fold the same
``attachments`` payload into the LLM call when an image- or PDF-bearing
turn reaches them. Centralizing the fold here keeps the three call sites
in sync and gives us one place to extend when we add new attachment types
(audio-as-binary, video frames, etc.).

Attachments shape (from media_job after Supabase upload):
    [{"type": "image"|"document"|"audio"|..., "url": str,
      "caption": Optional[str], "filename": Optional[str],
      "content_type": Optional[str]}]

For ``image`` we emit ``image_url`` content parts (Supabase public URL).
For ``document`` with PDF content-type we fetch the bytes from the URL,
base64-encode them, and emit a ``file`` content part — OpenAI's chat
completions API supports PDFs via inline file_data, not via URL.
"""

import base64
import logging
from typing import Any, Dict, List, Optional, Union

import requests


_PDF_FETCH_TIMEOUT_S = 5.0
_PDF_MAX_BYTES = 8 * 1024 * 1024  # OpenAI per-file limit is generous; cap defensively.


def _fetch_pdf_data_url(url: str) -> Optional[str]:
    """Download a PDF from ``url`` and return it as a base64 data URL.

    Returns None on any failure — caller falls back to text-only content.
    The fetch is bounded by a short timeout and a max-byte cap so a
    misbehaving Supabase host can't stall the agent loop.
    """
    try:
        resp = requests.get(url, timeout=_PDF_FETCH_TIMEOUT_S, stream=True)
        resp.raise_for_status()
        chunks: List[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _PDF_MAX_BYTES:
                logging.warning(
                    "[MULTIMODAL] PDF exceeds %d bytes, skipping: %s",
                    _PDF_MAX_BYTES, url[:80],
                )
                return None
            chunks.append(chunk)
        data = b"".join(chunks)
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:application/pdf;base64,{encoded}"
    except Exception as exc:
        logging.warning(
            "[MULTIMODAL] PDF fetch failed for %s: %s", url[:80], exc,
        )
        return None


def build_user_content(
    text: str,
    attachments: Optional[List[Dict[str, Any]]],
) -> Union[str, List[Dict[str, Any]]]:
    """
    Return content for a HumanMessage.

    - No attachments → plain string (preserves the text-only fast path
      with zero behavior change).
    - With image attachments → list of content parts:
        [{"type": "text", "text": ...},
         {"type": "image_url", "image_url": {"url": ...}}, ...]
    - With PDF document attachments → list of content parts:
        [{"type": "text", "text": ...},
         {"type": "file", "file": {"filename": ..., "file_data": ...}}, ...]

    Unknown / unsupported attachment types are skipped silently — the
    model still sees the text. Caller logs upstream cover the diagnostic
    surface.
    """
    if not attachments:
        return text

    media_parts: List[Dict[str, Any]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_type = (att.get("type") or "").strip().lower()
        url = att.get("url")
        if att_type == "image" and url:
            media_parts.append({"type": "image_url", "image_url": {"url": url}})
        elif att_type == "document" and url:
            content_type = (att.get("content_type") or "").lower()
            if "pdf" not in content_type:
                # Non-PDF documents (DOCX, XLSX, etc.) aren't accepted
                # by the chat completions API. Skip — model sees text.
                continue
            data_url = _fetch_pdf_data_url(url)
            if not data_url:
                continue
            filename = att.get("filename") or "documento.pdf"
            media_parts.append({
                "type": "file",
                "file": {"filename": filename, "file_data": data_url},
            })

    if not media_parts:
        return text

    return [{"type": "text", "text": text}, *media_parts]
