import logging
import json
import re

import requests
from flask import current_app, jsonify

from app.utils.mock_mode import is_mock_mode, mock_send_message


def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")


def _split_for_twilio(text: str, limit: int = 1500) -> list:
    """
    Split a message into chunks at most `limit` characters each, preferring
    natural boundaries (blank lines, newlines, sentences, spaces). Twilio's
    WhatsApp sender rejects bodies over 1600 chars (error 21617); default
    1500 leaves margin for any transport overhead.
    """
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = -1
        for sep in ("\n\n", "\n", ". ", " "):
            idx = window.rfind(sep)
            if idx > limit * 0.5:
                split_at = idx + len(sep)
                break
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def get_text_message_input(recipient, text):
    # Ensure recipient is a valid phone number format
    # Remove any non-digit characters except + at the beginning
    cleaned_recipient = re.sub(r'[^\d+]', '', recipient)

    # If it doesn't start with +, add it
    if not cleaned_recipient.startswith('+'):
        cleaned_recipient = '+' + cleaned_recipient

    logging.info(f"Original recipient: {recipient}")
    logging.info(f"Cleaned recipient: {cleaned_recipient}")

    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": cleaned_recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )


def get_audio_message_input(recipient, media_url, caption=None):
    """Build Meta-format payload for an audio message. Same recipient normalization as text."""
    cleaned_recipient = re.sub(r'[^\d+]', '', recipient)
    if not cleaned_recipient.startswith('+'):
        cleaned_recipient = '+' + cleaned_recipient
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": cleaned_recipient,
        "type": "audio",
        "audio": {"link": media_url},
    }
    if caption and str(caption).strip():
        payload["audio"]["caption"] = str(caption).strip()
    return json.dumps(payload)



def send_message(data, business_context=None):
    """
    Send message via WhatsApp API.
    Uses Meta Graph API by default; uses Twilio REST API when
    business_context has provider="twilio".

    In MOCK_MODE, this function logs the message instead of sending it.

    Args:
        data: JSON message payload (from get_text_message_input)
        business_context: Optional dict with phone_number_id (Meta) or
            provider="twilio" + twilio_phone_number (Twilio)
    """
    # Check if mock mode is enabled
    if is_mock_mode():
        return mock_send_message(data, business_context)

    # Twilio path: use Twilio REST API
    if business_context and business_context.get("provider") == "twilio":
        from_number = business_context.get("twilio_phone_number") or ""
        logging.info(f"[SEND] Using Twilio path (from={from_number})")
        try:
            import os
            account_sid = current_app.config.get("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_ACCOUNT_SID")
            auth_token = current_app.config.get("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN")
            from_number = business_context.get("twilio_phone_number") or (
                current_app.config.get("TWILIO_WHATSAPP_NUMBER") or os.getenv("TWILIO_WHATSAPP_NUMBER")
            )
            if from_number and not from_number.startswith("whatsapp:"):
                from_number = f"whatsapp:{from_number}"

            if not all([account_sid, auth_token, from_number]):
                logging.error("Missing Twilio credentials: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER")
                return None

            payload = json.loads(data) if isinstance(data, str) else data
            to_recipient = payload.get("to", "")
            to_whatsapp = f"whatsapp:{to_recipient}" if to_recipient and not str(to_recipient).startswith("whatsapp:") else to_recipient

            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            if payload.get("type") == "audio":
                audio_obj = payload.get("audio") or {}
                media_url = audio_obj.get("link") or ""
                body_text = (audio_obj.get("caption") or "").strip() or None
                if not media_url:
                    logging.error("[SEND] Twilio audio payload missing audio.link")
                    return None
                # WhatsApp/Twilio: avoid empty body with media (can trigger 63021); only send body if we have a caption
                create_kw = {"from_": from_number, "to": to_whatsapp, "media_url": [media_url]}
                if body_text:
                    create_kw["body"] = body_text
                msg = client.messages.create(**create_kw)
            else:
                body_text = (payload.get("text") or {}).get("body", "")
                # Twilio WhatsApp enforces a 1600-char limit per message (error 21617).
                # Split long responses at natural boundaries and send as multiple messages.
                chunks = _split_for_twilio(body_text, limit=1500)
                last_sid = None
                for chunk in chunks:
                    msg = client.messages.create(body=chunk, from_=from_number, to=to_whatsapp)
                    last_sid = msg.sid
                msg = type("Msg", (), {})()
                msg.sid = last_sid or ""

            mock_response = type("Response", (), {})()
            mock_response.status_code = 200
            mock_response.text = json.dumps({"sid": msg.sid})
            mock_response.headers = {}
            log_http_response(mock_response)
            return mock_response
        except Exception as e:
            logging.error(f"Twilio send_message failed: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return None

    # Meta path: use Graph API
    try:
        access_token = current_app.config['ACCESS_TOKEN']
        version = current_app.config['VERSION']
    except RuntimeError:
        import os
        access_token = os.getenv('ACCESS_TOKEN')
        version = os.getenv('VERSION', 'v18.0')

    # Use business-specific phone_number_id if available, otherwise use default
    if business_context and business_context.get('phone_number_id'):
        phone_number_id = business_context['phone_number_id']
        business_name = business_context.get('business', {}).get('name', 'Unknown')
        logging.info(f"[BUSINESS] Sending for business: {business_name} (phone_number_id: {phone_number_id})")
    else:
        # Fallback to default phone_number_id from environment
        try:
            phone_number_id = current_app.config['PHONE_NUMBER_ID']
        except RuntimeError:
            import os
            phone_number_id = os.getenv('PHONE_NUMBER_ID')
        logging.info(f"[BUSINESS] Using default phone_number_id from environment: {phone_number_id}")

    if not all([access_token, version, phone_number_id]):
        logging.error("Missing required credentials for WhatsApp API")
        return None

    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"

    try:
        logging.info(f"Sending message to WhatsApp API: {url}")
        logging.info(f"Headers: {headers}")
        logging.info(f"Data: {data}")

        response = requests.post(
            url, data=data, headers=headers, timeout=10
        )  # 10 seconds timeout as an example

        logging.info(f"Response status: {response.status_code}")
        logging.info(f"Response headers: {response.headers}")
        logging.info(f"Response body: {response.text}")

        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code

        # Process the response as normal
        log_http_response(response)
        return response

    except requests.Timeout:
        logging.error("Timeout occurred while sending message")
        return None
    except requests.HTTPError as e:
        logging.error(f"HTTP Error occurred: {e}")
        logging.error(f"Response status: {e.response.status_code if e.response else 'No response'}")
        logging.error(f"Response body: {e.response.text if e.response else 'No response body'}")
        return None
    except requests.RequestException as e:  # This will catch any general request exception
        logging.error(f"Request failed due to: {e}")
        return None


def send_twilio_cta(
    content_sid,
    variables,
    to,
    business_context=None,
):
    """
    Send a Twilio WhatsApp Content Template (e.g. twilio/call-to-action)
    by ContentSid. Used for the welcome message so customers see a
    button-styled CTA instead of a raw URL. Inside the 24h customer-care
    window no Meta template approval is required.

    Returns the Twilio Message object on success, None on failure.
    """
    if is_mock_mode():
        return mock_send_message(
            json.dumps({"to": to, "content_sid": content_sid, "content_variables": variables}),
            business_context,
        )
    try:
        import os
        account_sid = current_app.config.get("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = current_app.config.get("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN")
        from_number = (business_context or {}).get("twilio_phone_number") or (
            current_app.config.get("TWILIO_WHATSAPP_NUMBER") or os.getenv("TWILIO_WHATSAPP_NUMBER")
        )
        if from_number and not str(from_number).startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"
        if not all([account_sid, auth_token, from_number, content_sid]):
            logging.error("[CTA_SEND] Missing credentials or content_sid")
            return None
        to_whatsapp = f"whatsapp:{to}" if to and not str(to).startswith("whatsapp:") else to

        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        msg = client.messages.create(
            from_=from_number,
            to=to_whatsapp,
            content_sid=content_sid,
            content_variables=json.dumps(variables or {}),
        )
        logging.warning(
            f"[CTA_SEND] ✅ sent content_sid={content_sid} to={to_whatsapp} msg_sid={msg.sid}"
        )
        return msg
    except Exception as e:
        logging.error(f"[CTA_SEND] ❌ send failed: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return None


def process_text_for_whatsapp(text):
    logging.info(f"Processing text for WhatsApp: '{text}'")

    if not text:
        logging.warning("Empty text received, using fallback message")
        return "Gracias por tu mensaje. Te responderé pronto."

    # Remove brackets
    pattern = r"\【.*?\】"
    # Substitute the pattern with an empty string
    text = re.sub(pattern, "", text).strip()

    # Pattern to find double asterisks including the word(s) in between
    pattern = r"\*\*(.*?)\*\*"

    # Replacement pattern with single asterisks
    replacement = r"*\1*"

    # Substitute occurrences of the pattern with the replacement
    whatsapp_style_text = re.sub(pattern, replacement, text)

    # WhatsApp has a 4096 character limit for text messages
    if len(whatsapp_style_text) > 4096:
        whatsapp_style_text = whatsapp_style_text[:4093] + "..."

    # Remove any null characters or other problematic characters
    whatsapp_style_text = whatsapp_style_text.replace('\x00', '').replace('\u0000', '')

    # Ensure the text is not empty
    if not whatsapp_style_text.strip():
        logging.warning("Text became empty after processing, using fallback message")
        whatsapp_style_text = "Gracias por tu mensaje. Te responderé pronto."

    logging.info(f"Final processed text: '{whatsapp_style_text}'")
    return whatsapp_style_text


def extract_message_id(body):
    """
    Extract message ID from WhatsApp webhook payload.

    Args:
        body: Webhook payload from WhatsApp

    Returns:
        Message ID string or None if not found
    """
    try:
        value = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        messages = value.get("messages", [])
        if messages and len(messages) > 0:
            message_id = messages[0].get("id")
            return message_id
    except (KeyError, IndexError, TypeError) as e:
        logging.warning(f"[DEDUPE] Could not extract message ID: {e}")
    return None


def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )
