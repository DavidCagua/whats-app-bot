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


def is_valid_twilio_message(form_data: dict) -> bool:
    """
    Check if the incoming Twilio webhook has a valid message structure.
    Requires Body (message text) and From or WaId (sender).
    """
    if not form_data:
        return False
    body = form_data.get("Body")
    from_addr = form_data.get("From") or form_data.get("WaId")
    return body is not None and body != "" and bool(from_addr)


def normalize_twilio_to_meta(form_data: dict) -> dict:
    """
    Convert Twilio form-urlencoded webhook payload to Meta-style structure.

    This allows process_whatsapp_message and is_valid_whatsapp_message to work
    without modification. The output matches the Meta Cloud API webhook format.

    Args:
        form_data: Twilio POST form data (e.g. request.form as dict)

    Returns:
        Meta-style dict with entry/changes/value/messages structure
    """
    wa_id = _extract_wa_id(form_data)
    message_sid = form_data.get("MessageSid", "")
    body = form_data.get("Body", "")

    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": wa_id}],
                            "messages": [
                                {
                                    "id": message_sid,
                                    "text": {"body": body},
                                    "type": "text",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
