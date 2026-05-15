"""
Terminator tool + envelope schema for the chained order-agent
architecture (action agent → response renderer).

The action agent must end every turn by calling ``respond(...)``. The
agent's dispatch loop intercepts that call before tool execution, lifts
the envelope out, and hands it to the response renderer (which decides
text vs CTA vs template, applies business voice, and produces the final
user-facing string/payload).

Why a "terminator tool" instead of structured output via
``with_structured_output``?
- Tools are the model's native primitive — same path as every other
  tool the agent already calls. No second LLM call to parse JSON, no
  schema-fight at the response boundary.
- The model's habit ("end every turn with respond(...)") is enforced by
  the system prompt and the dispatch loop (no respond → fall back to a
  ``chat`` envelope synthesized from the model's prose).

Envelope kinds (the renderer maps these to channel primitives):
    items_added            — new items just landed in the cart
    items_removed          — item removed
    cart_updated           — quantity / notes changed
    cart_view              — user asked to see cart
    delivery_info_collected — saved partial delivery data
    ready_to_confirm       — all data present; renderer emits the CTA card
    order_placed           — final receipt
    menu_info              — listed categories / products
    product_info           — answered a single-product question
    disambiguation         — multiple variants matched, ask user to pick
    info                   — out-of-flow info (hours, address, etc.)
    out_of_scope           — handoff hint or non-order topic
    error                  — graceful apology
    chat                   — generic conversational reply

Note: ``injected_business_context`` is intentionally absent. ``respond``
needs no business state — it only emits a structured envelope. Keeping
it argument-free keeps the model's tool schema small and unambiguous.
"""

from typing import List, Optional

from langchain.tools import tool


VALID_RESPONSE_KINDS = (
    "items_added",
    "items_removed",
    "cart_updated",
    "cart_view",
    "delivery_info_collected",
    "ready_to_confirm",
    "order_placed",
    "menu_info",
    "product_info",
    "disambiguation",
    "info",
    "out_of_scope",
    "error",
    "chat",
)


@tool
def respond(
    kind: str,
    summary: str,
    facts: Optional[List[str]] = None,
) -> str:
    """
    End this turn. ALWAYS call this exactly once at the end of every turn,
    after any data/cart tools you used. The system composes the final
    user-facing message from the envelope you provide here — you do NOT
    write the user-facing prose yourself.

    Args:
        kind: One of items_added | items_removed | cart_updated | cart_view |
              delivery_info_collected | ready_to_confirm | order_placed |
              menu_info | product_info | disambiguation |
              info | out_of_scope | error | chat.
        summary: Short factual description of what happened this turn,
                 in your own words. Stay grounded — only state facts you
                 actually observed via tool results.
        facts: Optional list of verbatim strings (product names, prices,
               order IDs, addresses) the renderer is allowed to quote.
               Use this for anything numeric or named that must be exact.
    """
    # The agent dispatch loop intercepts respond() calls and never
    # actually invokes this body. Sentinel return is a belt-and-suspenders
    # signal in case it ever does.
    return "RESPOND_OK"
