from functools import wraps
from flask import current_app, jsonify, request
import logging
import hashlib
import hmac
import os


def twilio_signature_required(f):
    """
    Decorator to validate incoming Twilio webhook requests using X-Twilio-Signature.
    Skips validation in MOCK_MODE for local testing.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if os.getenv("MOCK_MODE", "false").lower() == "true":
            logging.info("[MOCK MODE] Skipping Twilio signature verification")
            return f(*args, **kwargs)

        try:
            auth_token = current_app.config.get("TWILIO_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN")
            if not auth_token:
                logging.error("[TWILIO] TWILIO_AUTH_TOKEN not configured")
                return jsonify({"status": "error", "message": "Twilio not configured"}), 503

            from twilio.request_validator import RequestValidator
            validator = RequestValidator(auth_token)
            signature = request.headers.get("X-Twilio-Signature", "")
            url = request.url
            params = dict(request.form) if request.form else {}

            logging.info(
                "[TWILIO] Validating signature: url=%s, has_signature=%s, param_count=%d",
                url, bool(signature), len(params)
            )

            if not validator.validate(url, params, signature):
                logging.warning(
                    "[TWILIO] Signature validation FAILED. Check: 1) TWILIO_AUTH_TOKEN matches Console "
                    "2) URL matches Twilio webhook config (https, correct domain) 3) Proxy headers"
                )
                return jsonify({"status": "error", "message": "Invalid signature"}), 403

            logging.info("[TWILIO] Signature validation passed")
            return f(*args, **kwargs)
        except Exception as e:
            logging.error("[TWILIO] Signature validation error: %s", e, exc_info=True)
            return jsonify({"status": "error", "message": "Validation failed"}), 403

    return decorated_function


def validate_signature(payload, signature):
    """
    Validate the incoming payload's signature against our expected signature
    """
    # Use the App Secret to hash the payload
    expected_signature = hmac.new(
        bytes(current_app.config["APP_SECRET"], "latin-1"),
        msg=payload.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Check if the signature matches
    return hmac.compare_digest(expected_signature, signature)


def signature_required(f):
    """
    Decorator to ensure that the incoming requests to our webhook are valid and signed with the correct signature.
    
    In MOCK_MODE, signature verification is skipped for local testing.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip signature verification in mock mode
        if os.getenv("MOCK_MODE", "false").lower() == "true":
            logging.info("[MOCK MODE] Skipping signature verification")
            return f(*args, **kwargs)
        
        signature = request.headers.get("X-Hub-Signature-256", "")[
            7:
        ]  # Removing 'sha256='
        if not validate_signature(request.data.decode("utf-8"), signature):
            logging.info("Signature verification failed!")
            return jsonify({"status": "error", "message": "Invalid signature"}), 403
        return f(*args, **kwargs)

    return decorated_function
