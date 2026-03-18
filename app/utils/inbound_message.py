"""
Unified inbound message model and parser.

Provider-agnostic: Twilio and Meta webhooks are normalized to the same shape,
then parsed into InboundMessage + attachments for storage and pipeline.
"""

from typing import Any, Dict, List, Optional


def parse_inbound_message(body: dict, provider: str = "twilio") -> Optional[Dict[str, Any]]:
    """
    Parse webhook body into a unified InboundMessage dict.

    Args:
        body: Normalized webhook payload (Meta-style: entry[0].changes[0].value, messages[0])
        provider: "twilio" or "meta"

    Returns:
        InboundMessage dict with from_wa_id, provider_message_id, text, attachments[];
        or None if body structure is invalid.
    """
    try:
        value = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    except (IndexError, KeyError, TypeError):
        return None

    contacts = value.get("contacts") or []
    messages = value.get("messages") or []
    if not messages:
        return None

    msg = messages[0]
    from_wa_id = (contacts[0].get("wa_id") if contacts else None) or ""
    provider_message_id = msg.get("id") or ""

    # Text
    text = ""
    if "text" in msg and isinstance(msg["text"], dict):
        text = (msg["text"].get("body") or "").strip()

    # Attachments from normalized payload (Twilio normalizer or future Meta)
    attachments: List[Dict[str, Any]] = []
    if msg.get("attachments"):
        for a in msg["attachments"]:
            attachments.append({
                "type": a.get("type") or "document",
                "content_type": a.get("content_type") or "",
                "provider_media_url": a.get("provider_media_url"),
                "provider_media_id": a.get("provider_media_id"),
                "size": a.get("size"),
                "duration_sec": a.get("duration_sec"),
                "url": None,
                "transcript": None,
                "provider_metadata": a.get("provider_metadata") or {},
            })

    # Meta native format: type audio/image with .audio/.image object (id or url)
    if not attachments and provider == "meta":
        if msg.get("type") == "audio" and "audio" in msg:
            audio = msg["audio"]
            attachments.append({
                "type": "audio",
                "content_type": (audio.get("mime_type") or "audio/ogg"),
                "provider_media_url": audio.get("url"),
                "provider_media_id": audio.get("id"),
                "size": None,
                "duration_sec": None,
                "url": None,
                "transcript": None,
                "provider_metadata": {"meta_audio": audio},
            })
        elif msg.get("type") == "image" and "image" in msg:
            image = msg["image"]
            attachments.append({
                "type": "image",
                "content_type": (image.get("mime_type") or "image/jpeg"),
                "provider_media_url": image.get("url"),
                "provider_media_id": image.get("id"),
                "size": None,
                "duration_sec": None,
                "url": None,
                "transcript": None,
                "provider_metadata": {"meta_image": image},
            })

    return {
        "provider": provider,
        "from_wa_id": from_wa_id,
        "provider_message_id": provider_message_id,
        "text": text,
        "attachments": attachments,
    }
