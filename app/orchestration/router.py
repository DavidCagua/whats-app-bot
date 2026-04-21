"""
Turn router.

Sits between the webhook handler and the agent dispatcher. Decides
whether a message can be answered by a shared capability (greeting,
later: catalog, business info) or must go through an agent.

Current scope (Phase 1a):
- Pure-greeting fast-path: returns the business greeting directly,
  no LLM call, no agent dispatch.
- Everything else: returns None → caller falls back to the existing
  single-agent dispatch through conversation_manager.

Later phases will add:
- LLM-based domain classifier (order / support / booking / marketing).
- Mixed-intent decomposition into multiple (agent, segment) tuples.
- Catalog fast-path for menu queries.
"""

from dataclasses import dataclass
from typing import Optional

from ..services import business_greeting


@dataclass
class RouterResult:
    """
    Outcome of router.route().

    - If `direct_reply` is set: the router produced a complete user-
      facing response itself (e.g. greeting template). Caller sends
      this verbatim and skips agent dispatch.
    - If `direct_reply` is None: router did not short-circuit. Caller
      must run the normal agent pipeline.

    Future fields (not used yet):
    - `segments`: list of (agent_type, segment) tuples for multi-agent
      dispatch.
    - `skip_llm_for`: hints for downstream optimization.
    """

    direct_reply: Optional[str] = None


def route(
    message_body: str,
    business_context: Optional[dict],
    customer_name: Optional[str],
) -> RouterResult:
    """
    Classify the message and decide how to respond.

    For Phase 1a, the only fast-path is pure greetings. Any non-greeting
    message returns an empty RouterResult (direct_reply=None), signaling
    the caller to fall through to the existing agent pipeline.
    """
    if business_greeting.is_pure_greeting(message_body):
        reply = business_greeting.get_greeting(business_context, customer_name)
        return RouterResult(direct_reply=reply)

    return RouterResult()
