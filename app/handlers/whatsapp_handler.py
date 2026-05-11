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


def persist_inbound_user_message(
    body,
    business_context=None,
    abort_key=None,
):
    """Persist the inbound user message + enqueue media job for attachments.

    Called at webhook receipt time (BEFORE debounce buffering / agent
    dispatch). Owning the persist here — not at flush time — guarantees
    1:1 between Twilio inbound webhook and ``conversations.role='user'``
    row, even when the flusher coalesces multiple webhook entries into
    a single agent turn. Pre-Option-C, the flusher persisted the merged
    body each time, so an abort + requeue + fresh-burst would produce
    two rows where the second's text contained the first's as a prefix
    (the production "Pago por transferencia" duplicate, 2026-05-10).

    Returns the conv_id when an attachment row was created (so the
    caller can enqueue / track the media worker), otherwise None. Errors
    are swallowed — webhook ack must not be blocked by a persist hiccup.
    """
    try:
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
            except (KeyError, IndexError, TypeError) as exc:
                logging.error(f"[CONVERSATION] webhook-time parse failed: {exc}")
                return None

        wa_id = inbound["from_wa_id"]
        message_body = (inbound.get("text") or "").strip()
        attachments = inbound.get("attachments") or []
        inferred_business_id = (business_context or {}).get("business_id")
        inferred_whatsapp_number_id = (business_context or {}).get("whatsapp_number_id")

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
                    # Pass abort_key so the media job can set the
                    # processing flag while it runs vision — concurrent
                    # text messages arriving during the vision call hit
                    # the existing abort+requeue path and coalesce
                    # cleanly with the image turn.
                    enqueue_media_job(conv_id, abort_key=abort_key)
                except Exception as enq_e:
                    logging.error(f"[CONVERSATION] media job enqueue failed: {enq_e}")
            return conv_id

        conversation_service.store_conversation_message(
            wa_id=wa_id,
            message=message_body,
            role="user",
            business_id=inferred_business_id,
            whatsapp_number_id=inferred_whatsapp_number_id,
        )
        return None
    except Exception as exc:
        logging.error(f"[CONVERSATION] webhook-time persist failed: {exc}")
        return None


def process_whatsapp_message(body, business_context=None, abort_key=None, stale_turn=False):
    """
    Process incoming WhatsApp message: parse, optionally run agent and send reply.

    Persistence is NOT done here — it moved to webhook time
    (``persist_inbound_user_message``) so abort+requeue can't produce
    duplicate user rows. This function only parses, dispatches the agent,
    and sends the reply.

    Args:
        abort_key: Optional Redis key checked before sending — if set, a newer
            message arrived during processing and this response should be dropped.
        stale_turn: True when this message was queued behind another turn —
            the user sent it before seeing the bot's previous reply.

    Returns:
        bool: True iff the turn aborted (mid-turn abort signal or
            pre-send abort gate fired). The debounce flusher uses this
            to decide whether to reset the requeue-count backoff
            counter — a turn that aborted should NOT reset the counter.
            Defaults to False on every non-agent path (early returns,
            voice-only, attachments-only) so callers see the
            conservative answer.
    """
    overall_start = time.time()
    # Fresh per-turn memoization cache. Eliminates 2-4x redundant reads
    # of session / customer / product search across order_flow layers.
    turn_cache.begin_turn()
    was_aborted = False
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
                return False

        wa_id = inbound["from_wa_id"]
        message_body = (inbound.get("text") or "").strip()
        attachments = inbound.get("attachments") or []
        logging.warning(f"[DEBUG] Extracted wa_id: {wa_id}, text length: {len(message_body)}, attachments: {len(attachments)}")

        has_audio = any((a.get("type") or "") == "audio" for a in attachments)
        has_image = any((a.get("type") or "") == "image" for a in attachments)
        # Image messages — captioned or not — defer entirely to the media
        # job. Vision runs first; the result decides whether to send a
        # templated promo reply or to run the agent on the caption with
        # the image context already known. Running the agent here on the
        # caption alone would lose the image content and produce
        # incoherent replies (e.g. "la tiene?" → CHAT fallback because
        # the agent has no antecedent).
        # Audio-only behavior unchanged: voice-only skips the agent so the
        # transcription worker can run + reply.
        if has_image:
            logging.warning(
                "[MESSAGE] Image message (caption=%s): deferring entire turn to media job",
                bool(message_body),
            )
            return
        if not message_body and has_audio:
            logging.warning("[MESSAGE] Voice-only message: skipping agent (media job handles)")
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

        _send_ok, was_aborted = _run_agent_and_send(
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
    return was_aborted


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
) -> tuple:
    """Run conversation manager and send reply via utils.

    Returns (send_ok, was_aborted):
        send_ok: True iff the reply was successfully dispatched (or
            intentionally suppressed). False on abort, error, or send
            failure.
        was_aborted: True iff the turn aborted (mid-turn abort signal,
            pre-send abort gate, etc.). Distinct from send_ok=False
            because the caller (debounce flusher) needs to know the
            difference for backoff-counter accounting — a clean turn
            with a send error should NOT reset the requeue counter the
            way a clean delivered turn would, but it also shouldn't be
            counted as an abort.
    """
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
            abort_key=abort_key,
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

    # Agent returns __ABORTED__ when it detected a newer message after
    # the planner but before the executor. No state was mutated — skip
    # sending so the newer message's flusher processes cleanly.
    if response == "__ABORTED__":
        logging.warning("[ABORT] %s: aborted after planner, skipping send", wa_id)
        return (False, True)

    # Conversation manager already dispatched a rich message (e.g. Twilio
    # CTA Content Template for the welcome). Skip the normal text send so
    # we don't double-send the greeting.
    if response == "__SUPPRESS_SEND__":
        logging.warning("[SEND] %s: suppress-send sentinel, rich message already dispatched", wa_id)
        return (True, False)

    # Pre-send abort gate: covers paths that don't go through the agent
    # (greeting fast-path) and paths where the dispatcher's abort fallback
    # produces a generic "Lo siento, no pude procesar..." string. By the
    # time we reach this check, a newer message may have arrived during
    # the agent run / Twilio call. If so, drop this response — the newer
    # message's flusher will handle the coalesced thread cleanly.
    if abort_key:
        try:
            from app.services.debounce import check_abort, clear_abort
            if check_abort(abort_key):
                clear_abort(abort_key)
                logging.warning(
                    "[ABORT] %s: pre-send abort detected, dropping response (len=%d)",
                    wa_id, len(response or ""),
                )
                return (False, True)
        except Exception as exc:
            # Never let the abort check break the send path.
            logging.warning("[ABORT] %s: pre-send check failed: %s", wa_id, exc)

    processed_response = process_text_for_whatsapp(response)
    data = get_text_message_input(wa_id, processed_response)
    send_start = time.time()
    result = send_message(data, business_context=business_context)
    logging.warning(f"[TIMING] send_message took {time.time() - send_start:.3f}s")
    if result is None:
        logging.error("❌ Failed to send message to WhatsApp API")
        return (False, False)
    logging.warning("✅ Message sent successfully to WhatsApp API")
    return (True, False)


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
        send_ok, _was_aborted = _run_agent_and_send(
            wa_id=wa_id,
            message_body=message_text.strip(),
            name=name,
            business_context=business_context,
            message_id=None,
        )
        if send_ok:
            logging.warning("[VOICE_REPLY] Reply sent successfully for transcript")
        return send_ok
    except Exception as e:
        logging.error(f"[VOICE_REPLY] Error: {e}", exc_info=True)
        return False
