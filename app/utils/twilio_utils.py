"""
Twilio WhatsApp webhook utilities.

Converts Twilio form-urlencoded payloads to Meta-style structure so
process_whatsapp_message can be reused without modification.
"""

import os
import re
import logging
import requests
from typing import Optional


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


def resolve_twilio_business_context(to_number: str) -> dict:
    """
    Resolve the business context for an inbound Twilio webhook from the
    recipient (`To`) number. Used by both the debounce flusher and the
    sync fallback path so that business lookup can happen *after* the
    debounce window instead of before it.

    Returns a fully populated context dict. Falls back to a synthetic
    "twilio" placeholder context when no matching business is found.
    """
    from ..database.business_service import business_service

    business_context = business_service.get_business_context_by_phone_number(to_number)
    if not business_context:
        twilio_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        if twilio_number and not str(twilio_number).startswith("whatsapp:"):
            twilio_number = f"whatsapp:{twilio_number}"
        return {
            "provider": "twilio",
            "twilio_phone_number": twilio_number or "",
            "business": {"name": "Twilio"},
            "business_id": "twilio",
        }

    twilio_from = to_number if str(to_number).startswith("whatsapp:") else f"whatsapp:{to_number}"
    business_context["provider"] = "twilio"
    business_context["twilio_phone_number"] = twilio_from
    business_context.pop("phone_number_id", None)  # avoid Meta API
    return business_context


def send_typing_indicator(
    message_sid: str,
    twilio_account_sid: str,
    twilio_auth_token: str,
    timeout: int = 5
) -> bool:
    """
    Send WhatsApp typing indicator via Twilio API.
    
    This signals to the user that a response is being prepared, improving UX
    by reducing perceived wait time during agent processing.
    
    Args:
        message_sid: The MessageSid from incoming Twilio webhook (e.g., "SMxxxxxx").
                    Must be a valid Twilio Message SID (starts with "SM").
        twilio_account_sid: Your Twilio Account SID (from TWILIO_ACCOUNT_SID env var).
        twilio_auth_token: Your Twilio Auth Token (from TWILIO_AUTH_TOKEN env var).
        timeout: HTTP request timeout in seconds (default: 5). Prevents webhook hang.
    
    Returns:
        True if typing indicator sent successfully (HTTP 201), False otherwise.
        Note: Failures are logged but don't block message processing.
    
    Behavior:
        - Typing indicator auto-disappears after 25 seconds OR when reply is delivered
        - This is a fire-and-forget call (non-blocking, doesn't delay webhook response)
        - Zero additional cost on Twilio bill (not a billable API call)
    """
    if not message_sid or not message_sid.startswith("SM"):
        logging.warning(f"[TYPING] Invalid message_sid format: {message_sid}")
        return False
    
    try:
        endpoint = "https://messaging.twilio.com/v2/Indicators/Typing.json"
        
        response = requests.post(
            endpoint,
            auth=(twilio_account_sid, twilio_auth_token),
            data={
                "messageId": message_sid,
                "channel": "whatsapp"
            },
            timeout=timeout
        )
        
        if response.status_code == 201:
            logging.warning(f"[TYPING] ✅ Typing indicator sent for message {message_sid}")
            return True
        else:
            logging.error(
                f"[TYPING] ❌ Failed to send typing indicator: "
                f"{response.status_code} - {response.text}"
            )
            return False
            
    except requests.exceptions.Timeout:
        logging.error(f"[TYPING] ⏱️ Timeout sending typing indicator (timeout={timeout}s)")
        return False
    except Exception as e:
        logging.error(f"[TYPING] ❌ Error sending typing indicator: {e}")
        return False
