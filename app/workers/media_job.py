"""
Media job: download from provider URL, upload to our storage, update attachment row.
Enqueued after storing a message with attachments; runs in background so webhook returns fast.
Optional: transcribe audio via AssemblyAI (free tier) or OpenAI Whisper and set transcript.
"""

import logging
import os
import tempfile
import threading
from datetime import datetime
from typing import Optional

import requests


# Placeholders that conversation_service writes when an attachment-only
# message has no real caption text (empty body). The image-only handler
# treats these as "no caption" so it runs the vision pipeline instead
# of mistakenly assuming the agent already replied to a caption.
_MEDIA_PLACEHOLDERS = frozenset({"[media]", "[audio]", "[image]"})


def _extension_from_content_type(content_type: Optional[str]) -> str:
    """Map MIME type to file extension for storage path."""
    if not content_type:
        return "bin"
    ct = (content_type or "").lower().split(";")[0].strip()
    mime_map = {
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/webm": "webm",
        "audio/wav": "wav",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/webm": "webm",
    }
    return mime_map.get(ct, "bin")


def _download_media(provider_media_url: Optional[str], provider: str = "twilio") -> Optional[bytes]:
    """Download media from provider URL. Twilio URLs require Basic auth."""
    if not provider_media_url:
        return None
    auth = None
    if "twilio.com" in (provider_media_url or ""):
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        if sid and token:
            auth = (sid, token)
    try:
        r = requests.get(provider_media_url, auth=auth, timeout=60)
        r.raise_for_status()
        data = r.content
        logging.warning(f"[MEDIA_JOB] Download OK: {len(data)} bytes")
        return data
    except Exception as e:
        logging.error(f"[MEDIA_JOB] Download failed for {provider_media_url[:80]}...: {e}")
        return None


def _upload_to_supabase(
    data: bytes,
    path: str,
    content_type: Optional[str],
    bucket: str = "inbound-media",
) -> Optional[str]:
    """Upload bytes to Supabase Storage and return public URL. Returns None if not configured or upload fails."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        logging.warning("[MEDIA_JOB] SUPABASE_URL or SUPABASE_SECRET_KEY not set, skipping upload")
        return None
    try:
        from supabase import create_client
        client = create_client(url, key)
        file_options = {}
        if content_type:
            file_options["content-type"] = content_type
        # Supabase storage expects a file path (opens with open(file, "rb")), not BytesIO
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(data)
            f.flush()
            tmp_path = f.name
        try:
            logging.warning(f"[MEDIA_JOB] Supabase upload: bucket={bucket}, path={path}")
            client.storage.from_(bucket).upload(path=path, file=tmp_path, file_options=file_options)
            public_url = client.storage.from_(bucket).get_public_url(path)
            logging.warning(f"[MEDIA_JOB] Supabase upload OK: {public_url[:80]}...")
            return public_url
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        logging.error(f"[MEDIA_JOB] Supabase upload failed for {path}: {e}", exc_info=True)
        return None


# Language for voice transcription (Colombian barbería context)
TRANSCRIPTION_LANGUAGE = "es"


def _transcribe_audio(data: bytes, content_type: Optional[str]) -> Optional[str]:
    """Transcribe audio bytes (Spanish). Prefer AssemblyAI when set, else OpenAI Whisper."""
    ext = _extension_from_content_type(content_type)
    if ext not in ("mp3", "m4a", "wav", "webm", "ogg", "opus", "mp4", "mpeg", "mpga"):
        ext = "ogg" if ext in ("ogg", "opus") else "ogg"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(data)
        f.flush()
        fname = f.name
    try:
        # Prefer AssemblyAI (free tier: ~185h/month pre-recorded)
        api_key = os.getenv("ASSEMBLYAI_API_KEY")
        if api_key:
            try:
                import assemblyai as aai
                aai.settings.api_key = api_key
                config = aai.TranscriptionConfig(language_code=TRANSCRIPTION_LANGUAGE)
                transcriber = aai.Transcriber()
                transcript = transcriber.transcribe(fname, config=config)
                if transcript.status == aai.TranscriptStatus.error:
                    logging.warning(f"[MEDIA_JOB] AssemblyAI transcription error: {transcript.error}")
                    return None
                text = (transcript.text or "").strip() or None
                if text:
                    logging.warning(f"[MEDIA_JOB] AssemblyAI transcription OK: {len(text)} chars")
                return text
            except Exception as e:
                logging.warning(f"[MEDIA_JOB] AssemblyAI transcription failed: {e}")
                return None
        # Fallback: OpenAI Whisper
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logging.warning("[MEDIA_JOB] No ASSEMBLYAI_API_KEY or OPENAI_API_KEY set, skipping transcription")
            return None
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            with open(fname, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1", file=f, language=TRANSCRIPTION_LANGUAGE
                )
            text = result.text if hasattr(result, "text") else str(result)
            if text:
                logging.warning(f"[MEDIA_JOB] OpenAI transcription OK: {len(text)} chars")
            return text
        except Exception as e:
            logging.warning(f"[MEDIA_JOB] OpenAI transcription failed: {e}")
            return None
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def process_media_job(conversation_id: int, abort_key: Optional[str] = None) -> None:
    """
    Process all attachments for a conversation: download from provider_media_url,
    upload to Supabase Storage, update conversation_attachments.url.
    Optional: transcribe audio and update transcript.

    abort_key: when present (image messages), the image branch sets the
    Redis processing flag during vision so concurrent text messages
    abort+requeue and coalesce with this turn — same mechanism used for
    text↔text races. Format: "abort:{to_number}:{phone}".
    """
    logging.warning(f"[MEDIA_JOB] Starting for conversation_id={conversation_id}")
    try:
        from app.database.models import Conversation, ConversationAttachment, get_db_session

        session = get_db_session()
        try:
            conv = session.query(Conversation).filter(Conversation.id == conversation_id).first()
            if not conv:
                logging.warning(f"[MEDIA_JOB] Conversation {conversation_id} not found")
                return
            business_id = str(conv.business_id) if conv.business_id else "unknown"
            year_month = datetime.utcnow().strftime("%Y-%m")
            rows = (
                session.query(ConversationAttachment)
                .filter(ConversationAttachment.conversation_id == conversation_id)
                .all()
            )
        finally:
            session.close()

        if not rows:
            logging.warning(f"[MEDIA_JOB] No attachments for conversation_id={conversation_id}")
            return

        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "inbound-media")
        provider = "twilio" if any(
            (r.provider_media_url or "").find("twilio.com") >= 0 for r in rows
        ) else "meta"
        logging.warning(f"[MEDIA_JOB] Processing {len(rows)} attachment(s), bucket={bucket}, provider={provider}")

        for att in rows:
            if att.url:
                logging.debug(f"[MEDIA_JOB] Attachment {att.id} already has url, skip")
                continue
            provider_url = att.provider_media_url
            logging.warning(f"[MEDIA_JOB] Downloading attachment {att.id} from provider...")
            data = _download_media(provider_url, provider)
            if not data:
                logging.warning(f"[MEDIA_JOB] Download failed for attachment {att.id}, skipping")
                continue
            logging.warning(f"[MEDIA_JOB] Downloaded {len(data)} bytes for attachment {att.id}, uploading to Supabase...")
            ext = _extension_from_content_type(att.content_type)
            path = f"{business_id}/{year_month}/{conversation_id}/{att.id}.{ext}"
            public_url = _upload_to_supabase(data, path, att.content_type, bucket)
            if not public_url:
                logging.warning(f"[MEDIA_JOB] Upload failed for attachment {att.id}, skipping")
                continue
            logging.warning(f"[MEDIA_JOB] Upload OK for attachment {att.id}, url saved")

            # Update attachment url
            upd = get_db_session()
            try:
                row = upd.query(ConversationAttachment).filter(ConversationAttachment.id == att.id).first()
                if row:
                    row.url = public_url
                    row.updated_at = datetime.utcnow()
                    upd.commit()
                logging.info(f"[MEDIA_JOB] Updated attachment {att.id} url")
            except Exception as e:
                logging.error(f"[MEDIA_JOB] Failed to update attachment {att.id}: {e}")
                upd.rollback()
            finally:
                upd.close()

            # Optional: transcribe audio, then run agent and send reply
            if (att.type or "").lower() == "audio":
                logging.warning("[MEDIA_JOB] Transcribing audio...")
                transcript = _transcribe_audio(data, att.content_type)
                if not transcript:
                    logging.warning("[MEDIA_JOB] No transcript (missing API key or transcription failed), skipping reply")
                if transcript:
                    upd2 = get_db_session()
                    try:
                        r2 = upd2.query(ConversationAttachment).filter(ConversationAttachment.id == att.id).first()
                        if r2:
                            r2.transcript = transcript
                            r2.updated_at = datetime.utcnow()
                            upd2.commit()
                        logging.warning(f"[MEDIA_JOB] Updated attachment {att.id} transcript, running agent...")
                    except Exception as e:
                        logging.error(f"[MEDIA_JOB] Failed to update transcript {att.id}: {e}")
                        upd2.rollback()
                    finally:
                        upd2.close()
                    # Run bot on transcript and send reply (requires Flask app context)
                    try:
                        from app import create_app
                        from app.handlers.whatsapp_handler import run_agent_and_send_reply
                        app = create_app()
                        with app.app_context():
                            run_agent_and_send_reply(
                                wa_id=conv.whatsapp_id,
                                message_text=transcript,
                                business_id=str(conv.business_id),
                            )
                    except Exception as e:
                        logging.error(f"[MEDIA_JOB] Voice reply failed: {e}", exc_info=True)

            # Image path: vision runs first, then we decide whether to
            # reply via a templated promo confirmation or to run the
            # agent on the caption. Either way the webhook handler
            # already deferred this turn to us — caption-aware promo
            # context lives here, not in two competing paths.
            #
            # conversation_service.store_conversation_message_with_attachments
            # substitutes empty captions with "[media]" / "[audio]" so
            # the conversation row never has an empty message. Treat
            # those placeholders as "no real caption" so the vision
            # pipeline still runs.
            elif (att.type or "").lower() == "image":
                raw_message = (conv.message or "").strip()
                caption = "" if raw_message in _MEDIA_PLACEHOLDERS else raw_message
                logging.warning(
                    "[MEDIA_JOB] image attachment ready: raw_message=%r has_caption=%s url=%s",
                    raw_message, bool(caption), (public_url or "")[:60],
                )
                _handle_image_message(conv, public_url, caption, abort_key)

    except Exception as e:
        logging.error(f"[MEDIA_JOB] Error: {e}", exc_info=True)


def _handle_image_message(conv, image_url: str, caption: str, abort_key: Optional[str]) -> None:
    """
    Image-bearing turn dispatcher (replaces the older image-only handler).

    Flow:
      1. Set processing flag so concurrent text messages abort+requeue
         (cross-modal coordination via the existing debounce machinery).
      2. Run vision extraction.
      3. If is_promo_screenshot → templated promo reply (no agent).
      4. Else if caption present → run the agent on the caption.
      5. Else → friendly receipt.
      6. Always clear the processing flag.
    """
    from app.services.image_promo_extractor import extract_promo_from_image

    business_id = str(conv.business_id) if conv.business_id else ""
    wa_id = conv.whatsapp_id
    logging.warning(
        "[MEDIA_JOB] image handler invoked wa_id=%s business_id=%s caption=%r url=%s",
        wa_id, business_id, caption, (image_url or "")[:60],
    )
    if not (business_id and wa_id):
        logging.warning("[MEDIA_JOB] image: missing wa_id or business_id, bailing")
        return

    # Mark this turn as in-flight so a concurrent text webhook from the
    # same customer triggers ABORT + requeue (same primitive used for
    # text↔text races). Without this, "image" then "la tiene?" would
    # produce two disconnected replies.
    proc_set = _mark_processing_for_abort_key(abort_key)

    try:
        extracted = extract_promo_from_image(image_url)
        if not extracted:
            logging.warning(
                "[MEDIA_JOB] image: extractor returned None — falling back"
            )
            _dispatch_non_promo_path(wa_id, business_id, caption)
            return

        is_promo = bool(extracted.get("is_promo_screenshot"))
        logging.warning(
            "[MEDIA_JOB] image: extractor result is_promo=%s candidate=%r mentioned=%s",
            is_promo, extracted.get("candidate_name"), extracted.get("mentioned_products"),
        )
        if is_promo:
            _handle_promo_screenshot(wa_id, business_id, extracted)
        else:
            _dispatch_non_promo_path(wa_id, business_id, caption)
    finally:
        if proc_set:
            _clear_processing_for_abort_key(abort_key)


def _dispatch_non_promo_path(wa_id: str, business_id: str, caption: str) -> None:
    """When vision says it's not a promo screenshot:
      - With caption: run the agent on the caption (regular text-only flow).
      - Without caption: friendly receipt so the customer knows we got it."""
    if caption:
        logging.warning("[MEDIA_JOB] image: not a promo, running agent on caption=%r", caption)
        try:
            from app import create_app
            from app.handlers.whatsapp_handler import run_agent_and_send_reply
            app = create_app()
            with app.app_context():
                run_agent_and_send_reply(
                    wa_id=wa_id,
                    message_text=caption,
                    business_id=business_id,
                )
        except Exception as exc:
            logging.error("[MEDIA_JOB] caption agent run failed: %s", exc, exc_info=True)
    else:
        logging.warning("[MEDIA_JOB] image: not a promo and no caption — generic receipt")
        _send_image_receipt(wa_id, business_id)


def _mark_processing_for_abort_key(abort_key: Optional[str]) -> bool:
    """Set the Redis processing flag derived from the abort_key. Returns
    True when the flag was set (caller should clear in finally)."""
    parsed = _parse_abort_key(abort_key)
    if not parsed:
        return False
    to_number, phone = parsed
    try:
        from app.services.debounce import _get_redis, _processing_key, _PROCESSING_TTL
        r = _get_redis()
        if r is None:
            return False
        r.set(_processing_key(to_number, phone), "1", ex=_PROCESSING_TTL)
        return True
    except Exception as exc:
        logging.warning("[MEDIA_JOB] could not mark processing: %s", exc)
        return False


def _clear_processing_for_abort_key(abort_key: Optional[str]) -> None:
    parsed = _parse_abort_key(abort_key)
    if not parsed:
        return
    to_number, phone = parsed
    try:
        from app.services.debounce import _get_redis, _processing_key
        r = _get_redis()
        if r is None:
            return
        r.delete(_processing_key(to_number, phone))
    except Exception as exc:
        logging.warning("[MEDIA_JOB] could not clear processing: %s", exc)


def _parse_abort_key(abort_key: Optional[str]):
    """abort_key format: 'abort:{to_number}:{phone}'. Returns (to_number,
    phone) or None when the key is malformed (silent — abort coordination
    is best-effort, never crash the worker)."""
    if not abort_key or not abort_key.startswith("abort:"):
        return None
    rest = abort_key[len("abort:"):]
    try:
        to_number, phone = rest.rsplit(":", 1)
    except ValueError:
        return None
    if not (to_number and phone):
        return None
    return (to_number, phone)


def _handle_promo_screenshot(wa_id: str, business_id: str, extracted: dict) -> None:
    """Match the extracted promo against active promotions, persist the
    listed set into customer_service_context (so a follow-up "sí" routes
    via SELECT_LISTED_PROMO → handoff to order), and send a confirmation."""
    from app.services import promotion_service
    from app.database.session_state_service import session_state_service

    candidate_name = extracted.get("candidate_name") or ""
    mentioned = extracted.get("mentioned_products") or []

    # Two-pass match: candidate name first, then a join of mentioned
    # products as a fallback. The shared matcher tolerates noise.
    matches = []
    if candidate_name:
        matches = promotion_service.find_promo_by_query(business_id, candidate_name)
    if not matches and mentioned:
        matches = promotion_service.find_promo_by_query(
            business_id, " ".join(mentioned[:3]),
        )

    if len(matches) == 1:
        promo = matches[0]
        # Persist into the same slot the CS PROMOS_LIST flow uses, so a
        # follow-up "sí" / "dame esa" naturally resolves via
        # SELECT_LISTED_PROMO → handoff to order/ADD_PROMO_TO_CART.
        try:
            session_state_service.save(wa_id, business_id, {
                "customer_service_context": {
                    "last_intent": "GET_PROMOS",
                    "last_result_kind": "promos_list",
                    "last_listed_promos": [{"id": promo["id"], "name": promo["name"]}],
                },
            })
        except Exception as exc:
            logging.error("[MEDIA_JOB] failed to persist last_listed_promos: %s", exc)

        price_clause = ""
        if promo.get("fixed_price") is not None:
            price = int(float(promo["fixed_price"]))
            price_clause = f" — precio promo ${price:,}".replace(",", ".")
        elif promo.get("discount_amount") is not None:
            amt = int(float(promo["discount_amount"]))
            price_clause = f" — descuento ${amt:,}".replace(",", ".")
        elif promo.get("discount_pct") is not None:
            price_clause = f" — {int(promo['discount_pct'])}% off"

        reply = (
            f"¡Sí! Esa promo la tenemos: *{promo['name']}*{price_clause}.\n\n"
            f"¿Quieres que te la agregue al pedido?"
        )
        _send_text_reply(wa_id, reply, business_id)
        return

    if len(matches) >= 2:
        names = "\n".join(f"• {p['name']}" for p in matches[:5])
        reply = (
            "Vi tu imagen — varias promos coinciden:\n"
            f"{names}\n\n"
            "¿Cuál de esas quieres?"
        )
        _send_text_reply(wa_id, reply, business_id)
        return

    # No match — list what IS active so the customer has options.
    active = promotion_service.list_active_promos(business_id)
    if active:
        names = "\n".join(f"• {p['name']}" for p in active[:5])
        reply = (
            "Vi tu imagen pero esa promo no la tenemos activa hoy. "
            f"Las que sí tenemos son:\n{names}"
        )
    else:
        reply = (
            "Vi tu imagen pero por hoy no tenemos promos activas. "
            "¿En qué más te puedo ayudar?"
        )
    _send_text_reply(wa_id, reply, business_id)


def _send_image_receipt(wa_id: str, business_id: str) -> None:
    """Generic friendly receipt for non-promo images."""
    _send_text_reply(
        wa_id,
        "Recibí tu imagen 👀 ¿En qué te puedo ayudar? "
        "Si quieres ordenar o ver el menú, cuéntame.",
        business_id,
    )


def _send_text_reply(wa_id: str, text: str, business_id: str) -> bool:
    """Deterministic send (no agent run): resolves business_context,
    sends via the WhatsApp send infra, persists the assistant turn into
    conversation history. Used by the image-only branch where we want
    a templated reply, not an LLM round-trip."""
    logging.warning(
        "[MEDIA_JOB] _send_text_reply: wa_id=%s business_id=%s len=%d",
        wa_id, business_id, len(text or ""),
    )
    try:
        from app import create_app
        from app.database.business_service import business_service
        from app.database.conversation_service import conversation_service
        from app.utils.whatsapp_utils import (
            get_text_message_input,
            process_text_for_whatsapp,
            send_message,
        )

        app = create_app()
        with app.app_context():
            business_context = business_service.get_business_context_by_business_id(
                business_id,
            )
            if not business_context:
                logging.warning(
                    "[MEDIA_JOB] _send_text_reply: no business_context for %s", business_id,
                )
                return False
            processed = process_text_for_whatsapp(text)
            data = get_text_message_input(wa_id, processed)
            result = send_message(data, business_context=business_context)
            if result is None:
                logging.error("[MEDIA_JOB] _send_text_reply: send_message returned None")
                return False
            logging.warning("[MEDIA_JOB] _send_text_reply: send OK")
            try:
                conversation_service.store_conversation_message(
                    wa_id, text, "assistant", business_id=business_id,
                )
            except Exception as exc:
                logging.warning("[MEDIA_JOB] failed to store assistant reply: %s", exc)
            return True
    except Exception as exc:
        logging.error("[MEDIA_JOB] _send_text_reply failed: %s", exc, exc_info=True)
        return False


def enqueue_media_job(conversation_id: int, abort_key: Optional[str] = None) -> None:
    """Run media job in a background thread so webhook returns immediately.

    abort_key (optional): when provided for image messages, the image
    branch sets the Redis processing flag so concurrent text webhooks
    abort+requeue normally. Defaults to None for backward compat with
    audio-only callers."""
    thread = threading.Thread(
        target=process_media_job,
        args=(conversation_id,),
        kwargs={"abort_key": abort_key},
        daemon=True,
    )
    thread.start()
    logging.warning(
        f"[MEDIA_JOB] Enqueued conversation_id={conversation_id} "
        f"abort_key={'set' if abort_key else 'none'}"
    )
