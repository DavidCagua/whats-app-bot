"""
Twilio WhatsApp webhook utilities.

Converts Twilio form-urlencoded payloads to Meta-style structure so
process_whatsapp_message can be reused without modification.
"""

import re


def _extract_wa_id(form_data: dict) -> str:
    """Extract wa_id (phone number) from Twilio form data."""
    # WaId is WhatsApp-specific; From has whatsapp:+number format
    wa_id = form_data.get("WaId") or form_data.get("From", "")
    if isinstance(wa_id, str) and wa_id.startswith("whatsapp:"):
        wa_id = wa_id[9:].strip()  # Remove "whatsapp:" prefix
    # Ensure E.164-like format (digits, optionally leading +)
    wa_id = re.sub(r"[^\d+]", "", wa_id)
    if wa_id and not wa_id.startswith("+"):
        wa_id = "+" + wa_id
    return wa_id or ""


def _content_type_to_attachment_type(content_type: str) -> str:
    """Map MIME type to attachment type (audio, image, video, document)."""
    if not content_type:
        return "document"
    ct = (content_type or "").strip().lower()
    if ct.startswith("audio/"):
        return "audio"
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    return "document"


def is_valid_twilio_message(form_data: dict) -> bool:
    """
    Check if the incoming Twilio webhook has a valid message structure.
    Valid if (Body is non-empty) OR (NumMedia >= 1), and From/WaId present.
    """
    if not form_data:
        return False
    from_addr = form_data.get("From") or form_data.get("WaId")
    if not from_addr:
        return False
    body = form_data.get("Body")
    num_media = form_data.get("NumMedia")
    try:
        n = int(num_media) if num_media not in (None, "") else 0
    except (TypeError, ValueError):
        n = 0
    has_text = body is not None and str(body).strip() != ""
    has_media = n >= 1
    return has_text or has_media


def normalize_twilio_to_meta(form_data: dict) -> dict:
    """
    Convert Twilio form-urlencoded webhook payload to Meta-style structure.

    This allows process_whatsapp_message and is_valid_whatsapp_message to work
    without modification. The output matches the Meta Cloud API webhook format.
    Includes media: MediaUrl0, MediaContentType0, etc. as messages[0].attachments.
    """
    wa_id = _extract_wa_id(form_data)
    message_sid = form_data.get("MessageSid", "")
    body = (form_data.get("Body") or "").strip()

    num_media = form_data.get("NumMedia")
    try:
        n = int(num_media) if num_media not in (None, "") else 0
    except (TypeError, ValueError):
        n = 0

    attachments = []
    first_type = "text"
    for i in range(n):
        media_url = form_data.get(f"MediaUrl{i}")
        content_type = form_data.get(f"MediaContentType{i}") or ""
        if media_url:
            atype = _content_type_to_attachment_type(content_type)
            if first_type == "text" and i == 0:
                first_type = atype
            attachments.append({
                "type": atype,
                "content_type": content_type,
                "provider_media_url": media_url,
            })

    message_type = first_type if attachments else "text"
    msg_payload = {
        "id": message_sid,
        "text": {"body": body},
        "type": message_type,
    }
    if attachments:
        msg_payload["attachments"] = attachments

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id}],
                            "messages": [msg_payload],
                        }
                    }
                ]
            }
        ],
    }
