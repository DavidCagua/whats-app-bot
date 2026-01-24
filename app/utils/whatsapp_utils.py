import logging
from flask import current_app, jsonify
import json
import requests

from app.services.langchain_service import langchain_service
from app.database.customer_service import customer_service
from app.utils.mock_mode import is_mock_mode, mock_send_message
import re
import time


def log_http_response(response):
    logging.info(f"Status: {response.status_code}")
    logging.info(f"Content-type: {response.headers.get('content-type')}")
    logging.info(f"Body: {response.text}")


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





def send_message(data, business_context=None):
    """
    Send message via WhatsApp API.
    Note: All businesses use the same Meta App (access_token from .env).
    Only phone_number_id differs per business.
    
    In MOCK_MODE, this function logs the message instead of sending it.

    Args:
        data: JSON message payload
        business_context: Optional dict with phone_number_id for routing
    """
    # Check if mock mode is enabled
    if is_mock_mode():
        return mock_send_message(data, business_context)
    
    # Get shared credentials from environment (same for all businesses)
    try:
        access_token = current_app.config['ACCESS_TOKEN']
        version = current_app.config['VERSION']
    except RuntimeError:
        # Not in Flask context, use environment variables directly
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


def process_whatsapp_message(body, business_context=None):
    """
    Process incoming WhatsApp message with optional business context.

    Args:
        body: Webhook payload from WhatsApp
        business_context: Optional dict with business info (business_id, access_token, etc.)
    """
    overall_start = time.time()
    try:
        logging.warning("[DEBUG] ========== PROCESSING MESSAGE ==========")
        wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
        logging.warning(f"[DEBUG] Extracted wa_id: {wa_id}")

        # Get customer name from database (NO fallback to WhatsApp display name)
        db_start = time.time()
        try:
            customer_data = customer_service.get_customer(wa_id)
            if customer_data and customer_data.get('name'):
                # Use database name
                name = customer_data['name']
                logging.info(f"[CUSTOMER] Using database name for {wa_id}: {name}")
            else:
                # Customer not in database or has no name - use generic greeting
                name = "Cliente"
                logging.info(f"[CUSTOMER] No database name for {wa_id}, using generic: {name}")
        except Exception as e:
            # Database query failed - use generic greeting
            logging.error(f"[CUSTOMER] Database lookup failed for {wa_id}: {e}, using generic name")
            import traceback
            logging.error(f"[CUSTOMER] Traceback: {traceback.format_exc()}")
            name = "Cliente"
        db_duration = time.time() - db_start
        logging.warning(f"[TIMING] Customer lookup took {db_duration:.3f}s")

        message = body["entry"][0]["changes"][0]["value"]["messages"][0]
        message_body = message["text"]["body"]

        # Log business context if available
        if business_context:
            logging.warning(f"[BUSINESS] Processing for: {business_context['business']['name']} (ID: {business_context['business_id']})")
        else:
            logging.warning("[BUSINESS] ⚠️ No business context, using default")

        logging.warning(f"[MESSAGE] Processing message from {name} ({wa_id}): {message_body}")

        # Extract message ID for tracing
        message_id = extract_message_id(body)
        
        # LangChain Integration with Calendar Tools
        llm_start = time.time()
        try:
            logging.warning("[DEBUG] Calling LangChain service...")
            response = langchain_service.generate_response(
                message_body, wa_id, name, 
                business_context=business_context,
                message_id=message_id
            )
            logging.warning(f"[DEBUG] Raw LangChain response: '{response}'")
            logging.warning(f"[DEBUG] Response length: {len(response) if response else 0}")
            logging.warning(f"[DEBUG] Response is empty: {not response or not response.strip()}")

            if not response:
                logging.error("❌ LangChain service returned None or empty response")
                response = "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"

        except Exception as e:
            logging.error(f"❌ Error in LangChain service: {e}")
            import traceback
            logging.error(f"[DEBUG] Traceback: {traceback.format_exc()}")
            response = "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
        llm_duration = time.time() - llm_start
        logging.warning(f"[TIMING] LangChain generate_response took {llm_duration:.3f}s")

        logging.warning(f"[DEBUG] Processing response for WhatsApp...")
        processed_response = process_text_for_whatsapp(response)
        logging.warning(f"[DEBUG] Processed response: '{processed_response}'")

        logging.warning(f"[DEBUG] Preparing message data...")
        data = get_text_message_input(wa_id, processed_response)
        logging.warning(f"[DEBUG] Message data: {data}")

        logging.warning(f"[DEBUG] Sending message to WhatsApp API...")
        send_start = time.time()
        result = send_message(data, business_context=business_context)
        send_duration = time.time() - send_start
        logging.warning(f"[TIMING] send_message took {send_duration:.3f}s")

        if result is None:
            logging.error("❌ Failed to send message to WhatsApp API")
        else:
            logging.warning("✅ Message sent successfully to WhatsApp API")
            logging.warning(f"[DEBUG] Response status: {result.status_code}")
            logging.warning(f"[DEBUG] Response body: {result.text}")

    except Exception as e:
        logging.error(f"❌ Error processing WhatsApp message: {e}")
        import traceback
        logging.error(f"[DEBUG] Full traceback: {traceback.format_exc()}")
        logging.error(f"[DEBUG] Message body: {json.dumps(body, indent=2)}")
    finally:
        total_duration = time.time() - overall_start
        logging.warning(f"[TIMING] process_whatsapp_message total took {total_duration:.3f}s")


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
