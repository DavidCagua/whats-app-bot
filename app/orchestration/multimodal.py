"""
Helper for building OpenAI-compatible multimodal user-message content.

The router and the CS / order agents all need to fold the same
``attachments`` payload into the LLM call when an image-bearing turn
reaches them. Centralizing the fold here keeps the three call sites in
sync and gives us one place to extend when we add new attachment types
(audio-as-binary, video frames, etc.).

Attachments shape (from media_job after Supabase upload):
    [{"type": "image"|"audio"|..., "url": str, "caption": Optional[str]}]
"""

from typing import Any, Dict, List, Optional, Union


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

    Unknown / unsupported attachment types are skipped silently — the
    model still sees the text. Caller logs upstream cover the diagnostic
    surface.
    """
    if not attachments:
        return text

    image_parts: List[Dict[str, Any]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_type = (att.get("type") or "").strip().lower()
        url = att.get("url")
        if att_type == "image" and url:
            image_parts.append({"type": "image_url", "image_url": {"url": url}})

    if not image_parts:
        return text

    return [{"type": "text", "text": text}, *image_parts]
