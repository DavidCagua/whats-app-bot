"""
WhatsApp message handling: view-layer orchestration.
Parses inbound message, persists, runs agent when allowed, and sends reply via utils.
Call send_message and other repeated flow from here (not from utils).
"""

import logging
import time
from typing import Optional

from app.database.business_service import business_service
from app.database.conversation_agent_service import conversation_agent_service
from app.database.conversation_service import conversation_service
from app.database.customer_service import customer_service
from app.orchestration import turn_cache
from app.orchestration.conversation_manager import conversation_manager
from app.utils.inbound_message import parse_inbound_message
from app.utils.whatsapp_utils import (
    extract_message_id,
    get_text_message_input,
    process_text_for_whatsapp,
    send_message,
)


def process_whatsapp_message(body, business_context=None, abort_key=None, stale_turn=False):
    """
    Process incoming WhatsApp message: parse, persist, optionally run agent and send reply.
    Voice-only messages are persisted and enqueued; reply is sent later when transcript is ready.

    Args:
        abort_key: Optional Redis key checked before sending — if set, a newer
            message arrived during processing and this response should be dropped.
        stale_turn: True when this message was queued behind another turn —
            the user sent it before seeing the bot's previous reply.
    """
    overall_start = time.time()
    # Fresh per-turn memoization cache. Eliminates 2-4x redundant reads
    # of session / customer / product search across order_flow layers.
    turn_cache.begin_turn()
    try:
        logging.warning("[DEBUG] ========== PROCESSING MESSAGE ==========")
        provider = "twilio" if (business_context or {}).get("provider") == "twilio" else "meta"
        inbound = parse_inbound_message(body, provider)
        if not inbound:
            try:
                value = body["entry"][0]["changes"][0]["value"]
                wa_id = value["contacts"][0]["wa_id"]
                msg = value["messages"][0]
                message_body = (msg.get("text") or {}).get("body") or ""
                inbound = {
                    "from_wa_id": wa_id,
                    "provider_message_id": msg.get("id") or "",
                    "text": message_body,
                    "attachments": [],
                }
            except (KeyError, IndexError, TypeError) as e:
                logging.error(f"[MESSAGE] Invalid webhook body: {e}")
                return

        wa_id = inbound["from_wa_id"]
        message_body = (inbound.get("text") or "").strip()
        attachments = inbound.get("attachments") or []
        logging.warning(f"[DEBUG] Extracted wa_id: {wa_id}, text length: {len(message_body)}, attachments: {len(attachments)}")

        inferred_business_id = (business_context or {}).get("business_id")
        inferred_whatsapp_number_id = (business_context or {}).get("whatsapp_number_id")

        try:
            if attachments:
                conv_id = conversation_service.store_conversation_message_with_attachments(
                    wa_id=wa_id,
                    message_text=message_body,
                    role="user",
                    attachments=attachments,
                    business_id=inferred_business_id,
                    whatsapp_number_id=inferred_whatsapp_number_id,
                )
                if conv_id is not None:
                    try:
                        from app.workers.media_job import enqueue_media_job
                        enqueue_media_job(conv_id)
                    except Exception as enq_e:
                        logging.error(f"[CONVERSATION] Failed to enqueue media job: {enq_e}")
            else:
                conversation_service.store_conversation_message(
                    wa_id=wa_id,
                    message=message_body,
                    role="user",
                    business_id=inferred_business_id,
                    whatsapp_number_id=inferred_whatsapp_number_id,
                )
        except Exception as e:
            logging.error(f"[CONVERSATION] Failed to store inbound user message: {e}")

        has_audio = any((a.get("type") or "") == "audio" for a in attachments)
        if not message_body and has_audio:
            logging.warning("[MESSAGE] Voice-only message: skipping agent (human will reply)")
            return

        if business_context:
            logging.warning(f"[BUSINESS] Processing for: {business_context['business']['name']} (ID: {business_context['business_id']})")
        else:
            logging.warning("[BUSINESS] ⚠️ No business context, using default")
        message_id = extract_message_id(body)
        name, agent_allowed = _agent_gate_and_name(wa_id, business_context)
        logging.warning(f"[MESSAGE] Processing message from {name} ({wa_id}): {message_body}")
        if not agent_allowed:
            return

        _run_agent_and_send(
            wa_id=wa_id,
            message_body=message_body,
            name=name,
            business_context=business_context,
            message_id=message_id,
            abort_key=abort_key,
            stale_turn=stale_turn,
        )

    except Exception as e:
        logging.error(f"❌ Error processing WhatsApp message: {e}")
        import traceback
        logging.error(traceback.format_exc())
    finally:
        logging.warning(f"[TIMING] process_whatsapp_message total took {time.time() - overall_start:.3f}s")


def _agent_gate_and_name(wa_id: str, business_context: Optional[dict]) -> tuple:
    """Resolve customer name and check if agent is allowed to run. Returns (name, allowed)."""
    db_start = time.time()
    try:
        # Go through the turn cache so downstream layers
        # (order_flow / order_tools) reuse this lookup instead of
        # hitting the DB again.
        customer_data = turn_cache.current().get_customer(
            wa_id, loader=lambda: customer_service.get_customer(wa_id)
        )
        name = (customer_data or {}).get("name") or "Cliente"
    except Exception as e:
        logging.error(f"[CUSTOMER] Database lookup failed for {wa_id}: {e}")
        name = "Cliente"
    logging.warning(f"[TIMING] Customer lookup took {time.time() - db_start:.3f}s")

    business_agent_enabled = True
    conversation_agent_enabled = True
    try:
        inferred_business_id = (business_context or {}).get("business_id")
        if inferred_business_id:
            business = business_service.get_business(inferred_business_id)
            settings = (business or {}).get("settings") or {}
            business_agent_enabled = settings.get("agent_enabled", True) is not False
            conversation_agent_enabled = conversation_agent_service.get_agent_enabled(
                inferred_business_id, wa_id
            )
        allowed = business_agent_enabled and conversation_agent_enabled
        if not allowed:
            logging.warning(
                "[AGENT] Agent disabled (business=%s conversation=%s); skipping automation",
                "on" if business_agent_enabled else "off",
                "on" if conversation_agent_enabled else "off",
            )
        return (name, allowed)
    except Exception as e:
        logging.error(f"[AGENT] Error checking enable flags (defaulting to enabled): {e}")
        return (name, True)


def _run_agent_and_send(
    wa_id: str,
    message_body: str,
    name: str,
    business_context: Optional[dict],
    message_id: Optional[str] = None,
    abort_key: Optional[str] = None,
    stale_turn: bool = False,
) -> bool:
    """Run conversation manager and send reply via utils. Returns True if send succeeded."""
    llm_start = time.time()
    try:
        logging.warning("[DEBUG] Calling ConversationManager...")
        response = conversation_manager.process(
            message_body=message_body,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
            stale_turn=stale_turn,
        )
        if not response or not response.strip():
            logging.error("❌ ConversationManager returned None or empty response")
            response = "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
    except Exception as e:
        logging.error(f"❌ Error in ConversationManager: {e}")
        import traceback
        logging.error(traceback.format_exc())
        response = "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo?"
    logging.warning(f"[TIMING] ConversationManager.process took {time.time() - llm_start:.3f}s")

    # ── Abort check: a newer message arrived while we were processing ──
    # Skip sending the stale response. The newer message's flusher will
    # process it with fresh context. The response IS stored in
    # conversation history (agent already persisted it) which is fine —
    # it gives the planner context that the bot "would have said X".
    if abort_key:
        from app.services.debounce import check_abort, clear_abort
        if check_abort(abort_key):
            clear_abort(abort_key)
            logging.warning(
                "[ABORT] %s: skipping send — newer message arrived during processing",
                wa_id,
            )
            return False

    processed_response = process_text_for_whatsapp(response)
    data = get_text_message_input(wa_id, processed_response)
    send_start = time.time()
    result = send_message(data, business_context=business_context)
    logging.warning(f"[TIMING] send_message took {time.time() - send_start:.3f}s")
    if result is None:
        logging.error("❌ Failed to send message to WhatsApp API")
        return False
    logging.warning("✅ Message sent successfully to WhatsApp API")
    return True


def run_agent_and_send_reply(wa_id: str, message_text: str, business_id: str) -> bool:
    """
    Run the conversation agent on message_text and send the reply to wa_id.
    Used when transcript is ready (e.g. after voice message transcription).
    Call from a thread that has Flask app context.
    """
    if not message_text or not message_text.strip():
        return False
    # Voice-reply path enters from a background worker thread — give it
    # its own fresh turn cache so it doesn't share state with whichever
    # request was served last on this thread.
    turn_cache.begin_turn()
    try:
        business_context = business_service.get_business_context_by_business_id(business_id)
        if not business_context:
            logging.warning("[VOICE_REPLY] No business context for business_id=%s", business_id)
            return False
        name, agent_allowed = _agent_gate_and_name(wa_id, business_context)
        if not agent_allowed:
            return False
        ok = _run_agent_and_send(
            wa_id=wa_id,
            message_body=message_text.strip(),
            name=name,
            business_context=business_context,
            message_id=None,
        )
        if ok:
            logging.warning("[VOICE_REPLY] Reply sent successfully for transcript")
        return ok
    except Exception as e:
        logging.error(f"[VOICE_REPLY] Error: {e}", exc_info=True)
        return False
