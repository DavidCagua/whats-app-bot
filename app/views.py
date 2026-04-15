import logging
import json
import os
import time

import threading

from flask import Blueprint, request, jsonify, current_app

from .decorators.security import signature_required, twilio_signature_required, admin_api_key_required
from .handlers.whatsapp_handler import process_whatsapp_message
from .utils.media_utils import convert_webm_to_ogg, upload_outbound_media_to_supabase
from .utils.whatsapp_utils import (
    is_valid_whatsapp_message,
    extract_message_id,
    get_text_message_input,
    get_audio_message_input,
    send_message,
)
from .database.business_service import business_service
from .database.conversation_service import conversation_service
from .database.booking_service import booking_service
from .services.debounce import debounce_message
from .services.message_deduplication import message_deduplication_service
from .services.turn_lock import wa_id_turn_lock
from .utils.twilio_utils import normalize_twilio_to_meta, is_valid_twilio_message, send_typing_indicator, resolve_twilio_business_context

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

        # Fire typing indicator in background — don't block the webhook.
        # The HTTP call to Twilio takes ~5s; blocking on it delays buffering
        # and breaks the debounce coalescing window.
        threading.Thread(
            target=send_typing_indicator,
            kwargs={
                "message_sid": message_sid,
                "twilio_account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
                "twilio_auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
            },
            daemon=True,
        ).start()

    # ── Debounce BEFORE business lookup ──────────────────────────────
    # The business lookup takes ~3–5 s (cold SQLAlchemy session + a
    # full-table scan of whatsapp_numbers). Doing it here would eat the
    # entire 3 s debounce window before the first message is even
    # buffered in Redis. Instead we key the buffer by (To, phone) —
    # each business owns a unique Twilio number, so this preserves
    # tenant isolation — and resolve the business context inside the
    # flusher after the quiet window expires.
    to_number = form_data.get("To", "")
    normalized_body = normalize_twilio_to_meta(form_data)
    sender_wa_id = (form_data.get("From") or "").replace("whatsapp:", "").strip()
    logging.warning("[DEBUG] Valid Twilio message, processing...")

    try:
        flask_app = current_app._get_current_object()
        buffered = debounce_message(sender_wa_id, to_number, normalized_body, flask_app)
        if buffered:
            # Return immediately — flusher handles the LLM call in background.
            logging.warning(
                f"[TIMING] handle_twilio_message (debounced) total took {time.time() - webhook_start:.3f}s"
            )
            return (
                '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                200,
                {"Content-Type": "text/xml"},
            )
    except Exception as e:
        logging.error(f"[DEBOUNCE] Unexpected error, falling back to sync: {e}")

    # ── Sync fallback (Redis unavailable) ────────────────────────────
    # Only now do we pay for the business lookup. Per-wa_id turn
    # serialization — see app/services/turn_lock.py.
    try:
        business_context = resolve_twilio_business_context(to_number)
        processing_start = time.time()
        with wa_id_turn_lock(sender_wa_id):
            process_whatsapp_message(normalized_body, business_context=business_context)
        logging.warning(
            f"[TIMING] process_whatsapp_message (Twilio sync) took {time.time() - processing_start:.3f}s"
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


@webhook_blueprint.route("/admin/send-message", methods=["POST"])
@admin_api_key_required
def admin_send_message():
    """
    Internal admin endpoint to send a WhatsApp message (text or audio) via Meta/Twilio path
    and persist it to the conversations table.
    Accepts optional media_url and caption; requires either text or media_url.
    """
    body = request.get_json(silent=True) or {}
    whatsapp_id = body.get("whatsapp_id")
    business_id = body.get("business_id")
    text = (body.get("text") or "").strip()
    media_url = (body.get("media_url") or "").strip()
    caption = (body.get("caption") or "").strip()
    phone_number_id = body.get("phone_number_id")
    phone_number = body.get("phone_number")

    if not whatsapp_id or not business_id:
        logging.warning("[ADMIN SEND] 400: missing whatsapp_id or business_id")
        return jsonify({"status": "error", "message": "whatsapp_id and business_id are required"}), 400
    if not text and not media_url:
        logging.warning("[ADMIN SEND] 400: need either text or media_url")
        return jsonify({"status": "error", "message": "Either text or media_url is required"}), 400
    if media_url and text and not caption:
        # If both provided, treat text as caption for audio
        caption = text
        text = ""

    if phone_number_id:
        business_context = business_service.get_business_context(phone_number_id)
    elif phone_number and str(phone_number).strip():
        business_context = business_service.get_business_context_by_phone_number(phone_number.strip())
    else:
        business_context = business_service.get_business_context_by_business_id(business_id)
    if not business_context:
        logging.warning(
            "[ADMIN SEND] 400: no WhatsApp number for business_id=%s (phone_number_id=%s, phone_number=%s)",
            business_id, phone_number_id or "(none)", phone_number or "(none)"
        )
        return jsonify({"status": "error", "message": "No WhatsApp number for business"}), 400

    if media_url:
        data = get_audio_message_input(whatsapp_id, media_url, caption=caption or None)
        result = send_message(data, business_context=business_context)
        if result is None:
            return jsonify({"status": "error", "message": "Failed to send message"}), 503
        message_text = caption or "[audio]"
        conversation_service.store_conversation_message_with_attachments(
            wa_id=whatsapp_id,
            message_text=message_text,
            role="assistant",
            attachments=[{"type": "audio", "url": media_url}],
            business_id=business_id,
            whatsapp_number_id=business_context.get("whatsapp_number_id"),
        )
        return jsonify({"ok": True}), 200

    data = get_text_message_input(whatsapp_id, text)
    result = send_message(data, business_context=business_context)
    if result is None:
        return jsonify({"status": "error", "message": "Failed to send message"}), 503

    conversation_service.store_conversation_message(
        wa_id=whatsapp_id,
        message=text,
        role="assistant",
        business_id=business_id,
        whatsapp_number_id=business_context.get("whatsapp_number_id"),
    )

    return jsonify({"ok": True}), 200


@webhook_blueprint.route("/admin/upload-media", methods=["POST"])
@admin_api_key_required
def admin_upload_media():
    """
    Accept multipart file upload (e.g. audio/ogg, audio/mpeg), upload to Supabase Storage,
    return { "url": public_url }. Used for outbound voice notes.
    """
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "Missing 'file' in multipart body"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400
    business_id = (request.form.get("business_id") or "").strip() or "default"
    content_type = f.content_type or "audio/ogg"
    try:
        data = f.read()
    except Exception as e:
        logging.warning(f"[ADMIN UPLOAD] Read file failed: {e}")
        return jsonify({"status": "error", "message": "Failed to read file"}), 400
    if not data:
        return jsonify({"status": "error", "message": "Empty file"}), 400

    # Twilio/WhatsApp reject audio/webm (63021). Convert to OGG (Opus) when we detect WebM.
    ct_lower = (content_type or "").split(";")[0].strip().lower()
    filename_lower = (f.filename or "").lower()
    # WebM/EBML magic bytes (so we convert even if Content-Type was lost in proxy)
    is_webm_bytes = len(data) >= 4 and data[:4] == b"\x1aE\xdf\xa3"
    is_webm_type = "webm" in ct_lower or filename_lower.endswith(".webm") or is_webm_bytes

    if is_webm_type:
        logging.info("[ADMIN UPLOAD] WebM detected (ct=%s, filename=%s), converting to OGG", content_type, f.filename)
        converted = convert_webm_to_ogg(data)
        if converted:
            data, content_type = converted
            logging.info("[ADMIN UPLOAD] Converted WebM to OGG for Twilio/WhatsApp")
        else:
            return jsonify({
                "status": "error",
                "message": "Voice note format not supported. Install ffmpeg for WebM, or upload OGG/MP3.",
            }), 503

    public_url = upload_outbound_media_to_supabase(data, content_type, business_id)
    if not public_url:
        return jsonify({"status": "error", "message": "Upload failed (check Supabase config)"}), 503
    return jsonify({"url": public_url}), 200




# ============================================================================
# BOOKINGS API
# ============================================================================

@webhook_blueprint.route("/admin/bookings", methods=["GET"])
@admin_api_key_required
def list_bookings():
    """
    GET /admin/bookings?business_id=<uuid>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&status=confirmed
    List bookings for a business, optionally filtered by date range and status.
    """
    business_id = request.args.get("business_id")
    if not business_id:
        return jsonify({"error": "business_id is required"}), 400

    bookings = booking_service.list_bookings(
        business_id=business_id,
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        status=request.args.get("status"),
        limit=int(request.args.get("limit", 200)),
    )
    return jsonify({"bookings": bookings, "count": len(bookings)}), 200


@webhook_blueprint.route("/admin/bookings/<booking_id>", methods=["GET"])
@admin_api_key_required
def get_booking(booking_id):
    """GET /admin/bookings/<uuid> — fetch a single booking."""
    booking = booking_service.get_booking(booking_id)
    if not booking:
        return jsonify({"error": "Booking not found"}), 404
    return jsonify(booking), 200


@webhook_blueprint.route("/admin/bookings", methods=["POST"])
@admin_api_key_required
def create_booking():
    """
    POST /admin/bookings
    Body: { business_id, start_at, end_at, customer_id?, service_name?, status?, notes?, created_via? }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    required = ["business_id", "start_at", "end_at"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    booking = booking_service.create_booking(data)
    if not booking:
        return jsonify({"error": "Failed to create booking"}), 500

    return jsonify(booking), 201


@webhook_blueprint.route("/admin/bookings/<booking_id>", methods=["PATCH"])
@admin_api_key_required
def update_booking(booking_id):
    """
    PATCH /admin/bookings/<uuid>
    Body: any subset of { status, notes, service_name, start_at, end_at, customer_id }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    booking = booking_service.update_booking(booking_id, data)
    if not booking:
        return jsonify({"error": "Booking not found"}), 404

    return jsonify(booking), 200


# ============================================================================
# AVAILABILITY API
# ============================================================================

@webhook_blueprint.route("/admin/availability", methods=["GET"])
@admin_api_key_required
def get_availability():
    """
    GET /admin/availability?business_id=<uuid>
    Get all availability rules for a business.

    GET /admin/availability?business_id=<uuid>&date=YYYY-MM-DD
    Get available slots for a specific date.
    """
    business_id = request.args.get("business_id")
    if not business_id:
        return jsonify({"error": "business_id is required"}), 400

    target_date = request.args.get("date")
    if target_date:
        slots = booking_service.get_available_slots(business_id, target_date)
        return jsonify({"date": target_date, "slots": slots}), 200

    rules = booking_service.get_availability(business_id)
    return jsonify({"availability": rules}), 200


@webhook_blueprint.route("/admin/availability", methods=["PUT"])
@admin_api_key_required
def upsert_availability():
    """
    PUT /admin/availability?business_id=<uuid>
    Body: { rules: [{ day_of_week, open_time, close_time, slot_duration_minutes, is_active }] }
    Upserts availability rules for a business (one per day of week).
    """
    business_id = request.args.get("business_id")
    if not business_id:
        return jsonify({"error": "business_id is required"}), 400

    data = request.get_json(silent=True)
    if not data or "rules" not in data:
        return jsonify({"error": "Body must contain 'rules' array"}), 400

    rules = booking_service.upsert_availability(business_id, data["rules"])
    return jsonify({"availability": rules, "count": len(rules)}), 200
