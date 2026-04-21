"""
Dispatcher: runs one or more agents for a single user turn.

Sits between conversation_manager (which decides WHO should handle a
message) and agent_executor (which runs a single agent). Handles three
concerns that don't belong in either:

1. Multi-segment dispatch — when the router decomposes a mixed-intent
   message into multiple (agent_type, segment) pairs, run each in order.
2. Mid-turn handoffs — when an agent returns `handoff: {to, segment, context}`,
   run the target agent with that context and keep collecting outputs.
3. State persistence between hops — each agent's state_update is applied
   to the session before the next agent runs, so later agents see the
   mutations of earlier ones.

Safety constraints:
- MAX_HOPS = 3 caps total agent invocations per turn.
- Cycles are detected via handoff_chain: if agent X already ran this
  turn, a handoff targeting X is rejected with a warning.
- Per-hop abort check: if a new user message arrived mid-turn, the
  dispatcher bails early and the handler skips send.

Return shape: a single `DispatchResult` with the final user-facing reply
and the merged list of state updates. conversation_manager persists.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..database.session_state_service import session_state_service
from .agent_executor import execute_agent


logger = logging.getLogger(__name__)


# Hard ceiling on agent invocations per turn. Prevents runaway handoff
# chains and gives us a deterministic upper bound on latency.
MAX_HOPS = 3


@dataclass
class DispatchResult:
    """Final outcome of dispatch(). Caller (conversation_manager) reads .message."""

    # Ordered list of every agent output produced this turn.
    agent_outputs: List[Dict[str, Any]] = field(default_factory=list)

    # Final user-facing reply. Set by the composer (if >1 output) or
    # directly from the single agent output.
    message: str = ""

    # Ordered list of agent_types that ran — used for observability and
    # cycle detection. Includes both router-segment starts and handoff hops.
    handoff_chain: List[str] = field(default_factory=list)

    # True when the dispatcher bailed because an abort signal fired mid-turn.
    aborted: bool = False


def dispatch(
    segments: List[Tuple[str, str]],
    *,
    wa_id: str,
    name: str,
    business_context: Optional[Dict[str, Any]],
    message_id: Optional[str] = None,
    stale_turn: bool = False,
    abort_key: Optional[str] = None,
) -> DispatchResult:
    """
    Run the ordered list of (agent_type, segment) pairs. Handle handoffs
    in each agent's output. Return the merged final result.

    Args:
        segments: ordered list of (agent_type, segment_text). For current
            single-intent turns this has exactly one entry.
        Other kwargs mirror agent_executor.execute_agent.

    Returns:
        DispatchResult with .message set. Empty segments list returns
        an empty DispatchResult (caller will handle).
    """
    result = DispatchResult()
    if not segments:
        return result

    business_id = (business_context or {}).get("business_id") if business_context else None

    # Local mutable view of session state so later agents see updates from
    # earlier ones within the same turn. We still persist state_updates to
    # the DB inside the loop so crashes don't lose partial progress.

    for (agent_type, segment_text) in segments:
        if _is_aborted(abort_key):
            logger.warning("[DISPATCHER] abort detected before starting agent=%s", agent_type)
            result.aborted = True
            return result

        output = _run_agent(
            agent_type=agent_type,
            message_body=segment_text,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
            stale_turn=stale_turn,
            abort_key=abort_key,
        )
        result.agent_outputs.append(output)
        result.handoff_chain.append(agent_type)
        _persist_state_update(wa_id, business_id, output)

        # Follow handoff chain if the agent requested one. Capped by MAX_HOPS
        # across the whole turn (not per-segment) so a pathological chain
        # can't run N*MAX_HOPS agents.
        while output.get("handoff") and len(result.handoff_chain) < MAX_HOPS:
            if _is_aborted(abort_key):
                logger.warning("[DISPATCHER] abort detected mid-handoff chain")
                result.aborted = True
                return result

            hand = output["handoff"] or {}
            target = (hand.get("to") or "").strip()
            if not target:
                logger.warning("[DISPATCHER] handoff without 'to' field — ignoring")
                break

            if target in result.handoff_chain:
                logger.warning(
                    "[DISPATCHER] handoff cycle detected: %s -> %s (chain=%s)",
                    agent_type, target, result.handoff_chain,
                )
                break

            handoff_segment = (hand.get("segment") or segment_text)
            handoff_context = hand.get("context") or {}

            logger.warning(
                "[DISPATCHER] handoff: %s -> %s (chain_len=%d)",
                result.handoff_chain[-1], target, len(result.handoff_chain) + 1,
            )

            output = _run_agent(
                agent_type=target,
                message_body=handoff_segment,
                wa_id=wa_id,
                name=name,
                business_context=business_context,
                message_id=message_id,
                stale_turn=stale_turn,
                abort_key=abort_key,
                handoff_context=handoff_context,
            )
            result.agent_outputs.append(output)
            result.handoff_chain.append(target)
            _persist_state_update(wa_id, business_id, output)

        if len(result.handoff_chain) >= MAX_HOPS and output.get("handoff"):
            logger.warning(
                "[DISPATCHER] MAX_HOPS=%d reached, dropping remaining handoff(s)",
                MAX_HOPS,
            )

    result.message = _compose_final_message(result.agent_outputs)
    return result


# ── Helpers ────────────────────────────────────────────────────────

def _run_agent(
    *,
    agent_type: str,
    message_body: str,
    wa_id: str,
    name: str,
    business_context: Optional[Dict[str, Any]],
    message_id: Optional[str],
    stale_turn: bool,
    abort_key: Optional[str],
    handoff_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Invoke a single agent with consistent error handling."""
    try:
        output = execute_agent(
            agent_type=agent_type,
            message_body=message_body,
            wa_id=wa_id,
            name=name,
            business_context=business_context,
            message_id=message_id,
            stale_turn=stale_turn,
            abort_key=abort_key,
            handoff_context=handoff_context,
        )
        if not isinstance(output, dict):
            logger.error("[DISPATCHER] agent=%s returned non-dict: %r", agent_type, type(output))
            output = _error_output(agent_type, "bad return type")
    except Exception as exc:
        logger.error("[DISPATCHER] agent=%s raised: %s", agent_type, exc, exc_info=True)
        output = _error_output(agent_type, str(exc))
    return output


def _persist_state_update(
    wa_id: str,
    business_id: Optional[str],
    output: Dict[str, Any],
) -> None:
    """
    Write the agent's state_update to the session. Applied BETWEEN hops
    so the next agent reads post-mutation state. Failures are logged but
    don't abort dispatch — losing a state mutation is better than
    losing the user's reply.
    """
    state_update = output.get("state_update") or {}
    if not state_update or not wa_id or not business_id:
        return
    try:
        session_state_service.save(wa_id, str(business_id), state_update)
    except Exception as exc:
        logger.error("[DISPATCHER] state persistence failed for %s: %s", wa_id, exc)


def _is_aborted(abort_key: Optional[str]) -> bool:
    """Check the Redis abort flag. Imports are lazy to avoid circular refs."""
    if not abort_key:
        return False
    try:
        from ..services.debounce import check_abort
        return check_abort(abort_key)
    except Exception:
        return False


def _error_output(agent_type: str, error_message: str) -> Dict[str, Any]:
    """Safe fallback output when an agent crashes or misbehaves."""
    return {
        "agent_type": agent_type,
        "message": "",
        "state_update": {},
        "error": error_message,
    }


def _compose_final_message(outputs: List[Dict[str, Any]]) -> str:
    """
    Build the single user-facing reply from one or more agent outputs.

    - 0 outputs → empty string (caller handles).
    - 1 output → that agent's message verbatim. Composer skipped.
    - 2+ outputs → run the composer to merge prose cleanly.
    """
    # Filter out empty messages (e.g. aborted agents) but keep order.
    non_empty = [o for o in outputs if (o.get("message") or "").strip()]
    if not non_empty:
        return ""
    if len(non_empty) == 1:
        return non_empty[0]["message"].strip()

    # Lazy import so tests can stub composer independently and to avoid
    # spinning up the composer LLM for single-agent turns.
    from .response_composer import compose
    return compose([o["message"] for o in non_empty])
