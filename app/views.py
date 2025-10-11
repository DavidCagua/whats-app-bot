import logging
import json

from flask import Blueprint, request, jsonify, current_app

from .decorators.security import signature_required
from .utils.whatsapp_utils import (
    process_whatsapp_message,
    is_valid_whatsapp_message,
)
from .database.business_service import business_service

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
    # logging.info(f"request body: {body}")

    # Check if it's a WhatsApp status update
    if (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("statuses")
    ):
        logging.info("Received a WhatsApp status update.")
        return jsonify({"status": "ok"}), 200

    try:
        if is_valid_whatsapp_message(body):
            # Extract phone_number_id from webhook for multi-tenant routing
            phone_number_id = None
            try:
                metadata = body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")

                if phone_number_id:
                    logging.warning(f"[ROUTING] Extracted phone_number_id: {phone_number_id}")

                    # Get business context for this phone number
                    business_context = business_service.get_business_context(phone_number_id)

                    if business_context:
                        logging.warning(f"[ROUTING] Routing to business: {business_context['business']['name']} (ID: {business_context['business_id']})")
                        # Pass business context to message processor
                        process_whatsapp_message(body, business_context=business_context)
                    else:
                        logging.warning(f"[ROUTING] No business found for phone_number_id: {phone_number_id}. Using default business.")
                        # Fallback to default business
                        process_whatsapp_message(body, business_context=None)
                else:
                    logging.warning("[ROUTING] No phone_number_id in webhook. Using default business.")
                    process_whatsapp_message(body, business_context=None)

            except Exception as e:
                logging.error(f"[ROUTING] Error extracting business context: {e}")
                # Fallback to default business on error
                process_whatsapp_message(body, business_context=None)

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


