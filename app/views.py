import logging
import json
import time

from flask import Blueprint, request, jsonify, current_app

from .decorators.security import signature_required
from .utils.whatsapp_utils import (
    process_whatsapp_message,
    is_valid_whatsapp_message,
    extract_message_id,
)
from .database.business_service import business_service
from .services.message_deduplication import message_deduplication_service

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
            
            # Extract phone_number_id from webhook for multi-tenant routing
            # Try multiple locations where phone_number_id might be
            phone_number_id = None
            try:
                value = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
                
                # Try metadata first
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")
                
                # If not in metadata, try top-level value
                if not phone_number_id:
                    phone_number_id = value.get("phone_number_id")
                
                # If still not found, try in the entry level
                if not phone_number_id:
                    phone_number_id = body.get("entry", [{}])[0].get("phone_number_id")
                
                logging.warning(f"[DEBUG] Value object keys: {list(value.keys())}")
                logging.warning(f"[DEBUG] Metadata: {json.dumps(metadata, indent=2)}")
                logging.warning(f"[DEBUG] Extracted phone_number_id: {phone_number_id}")

                if phone_number_id:
                    logging.warning(f"[ROUTING] Extracted phone_number_id: {phone_number_id}")

                    # Get business context for this phone number
                    business_context = business_service.get_business_context(phone_number_id)

                    if business_context:
                        logging.warning(f"[ROUTING] ✅ Routing to business: {business_context['business']['name']} (ID: {business_context['business_id']})")
                        # Pass business context to message processor
                        processing_start = time.time()
                        process_whatsapp_message(body, business_context=business_context)
                        logging.warning(
                            f"[TIMING] process_whatsapp_message (with business_context) took {time.time() - processing_start:.3f}s"
                        )
                    else:
                        logging.warning(f"[ROUTING] ⚠️ No business found for phone_number_id: {phone_number_id}. Using default PHONE_NUMBER_ID from .env")
                        # Fallback to default business - still process the message!
                        processing_start = time.time()
                        process_whatsapp_message(body, business_context=None)
                        logging.warning(
                            f"[TIMING] process_whatsapp_message (default context) took {time.time() - processing_start:.3f}s"
                        )
                else:
                    logging.warning("[ROUTING] ⚠️ No phone_number_id in webhook. Using default PHONE_NUMBER_ID from .env")
                    logging.warning(f"[DEBUG] Full value object: {json.dumps(value, indent=2)}")
                    # Still process the message with default config
                    processing_start = time.time()
                    process_whatsapp_message(body, business_context=None)
                    logging.warning(
                        f"[TIMING] process_whatsapp_message (no phone_number_id) took {time.time() - processing_start:.3f}s"
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


@webhook_blueprint.route("/health", methods=["GET"])
def health():
    """Health check endpoint for Railway deployment."""
    return jsonify({"status": "ok", "service": "whatsapp-bot"}), 200


