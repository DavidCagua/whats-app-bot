import logging
import json
import os
import time

from flask import Blueprint, request, jsonify, current_app

from .decorators.security import signature_required, twilio_signature_required
from .utils.whatsapp_utils import (
    process_whatsapp_message,
    is_valid_whatsapp_message,
    extract_message_id,
)
from .database.business_service import business_service
from .services.message_deduplication import message_deduplication_service
from .utils.twilio_utils import normalize_twilio_to_meta, is_valid_twilio_message

webhook_blueprint = Blueprint("webhook", __name__)


def handle_message():
    """
    Handle incoming webhook events from the WhatsApp API.

    This function processes incoming WhatsApp messages and other events,
    such as delivery statuses. If the event is a valid message, it gets
    processed. If the incoming payload is not a recognized WhatsApp event,
    an error is returned.

    Every message send will trigger 4 HTTP requests to your webhook: message, sent, delivered, read.

    Returns:
        response: A tuple containing a JSON response and an HTTP status code.
    """
    body = request.get_json()
    webhook_start = time.time()
    logging.warning(f"[DEBUG] ========== INCOMING WEBHOOK ==========")
    logging.warning(f"[DEBUG] Full request body: {json.dumps(body, indent=2)}")

    # Check if it's a WhatsApp status update
    value = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
    statuses = value.get("statuses")
    
    if statuses:
        logging.warning("[STATUS] Received a WhatsApp status update.")
        for status in statuses:
            status_type = status.get("status", "unknown")
            message_id = status.get("id", "unknown")
            recipient = status.get("recipient_id", "unknown")
            
            if status_type == "failed":
                errors = status.get("errors", [])
                logging.error(f"[STATUS] ❌ Message FAILED to {recipient} (ID: {message_id})")
                for error in errors:
                    error_code = error.get("code")
                    error_title = error.get("title")
                    error_msg = error.get("message")
                    error_details = error.get("error_data", {}).get("details", "")
                    logging.error(f"[STATUS] Error {error_code}: {error_title} - {error_msg}")
                    if error_details:
                        logging.error(f"[STATUS] Details: {error_details}")
            else:
                logging.info(f"[STATUS] Message {status_type} to {recipient} (ID: {message_id})")
        
        logging.warning(
            f"[TIMING] handle_message (status update) took {time.time() - webhook_start:.3f}s"
        )
        return jsonify({"status": "ok"}), 200

    try:
        if is_valid_whatsapp_message(body):
            logging.warning("[DEBUG] Valid WhatsApp message detected")
            
            # Check for duplicate message (idempotency)
            message_id = extract_message_id(body)
            if message_id:
                if message_deduplication_service.is_duplicate(message_id):
                    logging.info(f"[DEDUPE] Duplicate message detected, skipping processing: {message_id}")
                    return jsonify({"status": "ok", "duplicate": True}), 200
                # Mark as processed before processing (prevents race conditions)
                message_deduplication_service.mark_as_processed(message_id)
            
            # Extract receiving phone number for routing (unified lookup for Meta/Twilio)
            value = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
            metadata = value.get("metadata", {})
            display_phone_number = metadata.get("display_phone_number")
            phone_number_id = metadata.get("phone_number_id") or value.get("phone_number_id")

            try:
                business_context = None
                if display_phone_number:
                    business_context = business_service.get_business_context_by_phone_number(display_phone_number)
                if not business_context and phone_number_id:
                    business_context = business_service.get_business_context(phone_number_id)

                if business_context:
                    logging.warning(f"[ROUTING] ✅ Routing to business: {business_context['business']['name']}")
                    processing_start = time.time()
                    process_whatsapp_message(body, business_context=business_context)
                    logging.warning(
                        f"[TIMING] process_whatsapp_message (with business_context) took {time.time() - processing_start:.3f}s"
                    )
                else:
                    logging.warning("[ROUTING] ⚠️ No business found for this number. Using default from .env")
                    processing_start = time.time()
                    process_whatsapp_message(body, business_context=None)
                    logging.warning(
                        f"[TIMING] process_whatsapp_message (default context) took {time.time() - processing_start:.3f}s"
                    )

            except Exception as e:
                logging.error(f"[ROUTING] ❌ Error extracting business context: {e}")
                import traceback
                logging.error(f"[ROUTING] Traceback: {traceback.format_exc()}")
                # Fallback to default business on error
                processing_start = time.time()
                process_whatsapp_message(body, business_context=None)
                logging.warning(
                    f"[TIMING] process_whatsapp_message (routing error) took {time.time() - processing_start:.3f}s"
                )

            logging.warning("[DEBUG] ========== WEBHOOK PROCESSING COMPLETE ==========")
            logging.warning(
                f"[TIMING] handle_message total took {time.time() - webhook_start:.3f}s"
            )
            return jsonify({"status": "ok"}), 200
        else:
            # if the request is not a WhatsApp API event, return an error
            return (
                jsonify({"status": "error", "message": "Not a WhatsApp API event"}),
                404,
            )
    except json.JSONDecodeError:
        logging.error("Failed to decode JSON")
        return jsonify({"status": "error", "message": "Invalid JSON provided"}), 400


def handle_twilio_message():
    """
    Handle incoming webhook events from Twilio WhatsApp.
    Normalizes form data to Meta format and reuses process_whatsapp_message.
    Returns TwiML empty response (bot replies async via REST API).
    """
    webhook_start = time.time()
    form_data = dict(request.form) if request.form else {}
    logging.warning("[DEBUG] ========== INCOMING TWILIO WEBHOOK ==========")
    logging.warning(f"[DEBUG] Twilio form data: {json.dumps(form_data, indent=2)}")

    if not is_valid_twilio_message(form_data):
        logging.warning("[TWILIO] Invalid Twilio message (missing Body or From)")
        return (
            '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            200,
            {"Content-Type": "text/xml"},
        )

    message_sid = form_data.get("MessageSid")
    if message_sid:
        if message_deduplication_service.is_duplicate(message_sid):
            logging.info(f"[DEDUPE] Duplicate Twilio message, skipping: {message_sid}")
            return (
                '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                200,
                {"Content-Type": "text/xml"},
            )
        message_deduplication_service.mark_as_processed(message_sid)

    # Look up business by receiving number (To)
    to_number = form_data.get("To", "")
    business_context = business_service.get_business_context_by_phone_number(to_number)
    if not business_context:
        twilio_number = current_app.config.get("TWILIO_WHATSAPP_NUMBER") or os.getenv("TWILIO_WHATSAPP_NUMBER")
        if twilio_number and not str(twilio_number).startswith("whatsapp:"):
            twilio_number = f"whatsapp:{twilio_number}"
        business_context = {
            "provider": "twilio",
            "twilio_phone_number": twilio_number or "",
            "business": {"name": "Twilio"},
            "business_id": "twilio",
        }
    else:
        # Message came via Twilio webhook - always send reply via Twilio (not Meta)
        twilio_from = to_number if str(to_number).startswith("whatsapp:") else f"whatsapp:{to_number}"
        business_context["provider"] = "twilio"
        business_context["twilio_phone_number"] = twilio_from
        business_context.pop("phone_number_id", None)  # avoid Meta API

    normalized_body = normalize_twilio_to_meta(form_data)
    logging.warning("[DEBUG] Valid Twilio message, processing...")

    try:
        processing_start = time.time()
        process_whatsapp_message(normalized_body, business_context=business_context)
        logging.warning(
            f"[TIMING] process_whatsapp_message (Twilio) took {time.time() - processing_start:.3f}s"
        )
    except Exception as e:
        logging.error(f"[TWILIO] Error processing message: {e}")
        import traceback
        logging.error(f"[TWILIO] Traceback: {traceback.format_exc()}")

    logging.warning(
        f"[TIMING] handle_twilio_message total took {time.time() - webhook_start:.3f}s"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        200,
        {"Content-Type": "text/xml"},
    )


# Required webhook verifictaion for WhatsApp
def verify():
    # Parse params from the webhook verification request
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    # Check if a token and mode were sent
    if mode and token:
        # Check the mode and token sent are correct
        if mode == "subscribe" and token == current_app.config["VERIFY_TOKEN"]:
            # Respond with 200 OK and challenge token from the request
            logging.info("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            # Responds with '403 Forbidden' if verify tokens do not match
            logging.info("VERIFICATION_FAILED")
            return jsonify({"status": "error", "message": "Verification failed"}), 403
    else:
        # Responds with '400 Bad Request' if verify tokens do not match
        logging.info("MISSING_PARAMETER")
        return jsonify({"status": "error", "message": "Missing parameters"}), 400


@webhook_blueprint.route("/webhook", methods=["GET"])
def webhook_get():
    return verify()

@webhook_blueprint.route("/webhook", methods=["POST"])
@signature_required
def webhook_post():
    return handle_message()


@webhook_blueprint.route("/webhook/twilio", methods=["POST"])
@twilio_signature_required
def webhook_twilio_post():
    return handle_twilio_message()


@webhook_blueprint.route("/health", methods=["GET"])
def health():
    """Health check endpoint for Railway deployment."""
    return jsonify({"status": "ok", "service": "whatsapp-bot"}), 200


