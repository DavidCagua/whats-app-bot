# Order Agent Rearchitecture Plan

**Status**: proposal. Pre-work decision document. Captures why the current architecture is failing, what we're moving to, and the migration phasing.

## TL;DR

Today's order agent is a **planner (LLM) → executor (deterministic switch) → response generator (LLM)** pipeline that emits a custom JSON-intent protocol. After multi-intent dispatch, the planner prompt grew to ~7k tokens and the model stopped following its own rules — bolting `CONFIRM` onto cart mutations, dropping clear orders to `CHAT`, echoing recap data as fake user input. Frontier models (`gpt-4o`) made it worse, not better.

We're moving to a **tool-calling agent + response generator** architecture (Option A) and layering deterministic short-circuits on top (Option B) once A is in place. A is the architectural fix. B is the optimization on top.

The planner-as-LLM and executor-as-switch collapse into "the model calls typed tools that own their preconditions". The state machine survives but moves *into* the tools.

## Why the current architecture is breaking

Production failures from May 2026 testing, all traced to the same root:

| Symptom | Cause |
|---|---|
| `"dos barracudas"` → `[ADD_TO_CART, CONFIRM]` → CTA card fires before customer sees cart recap | Planner pattern-matches few-shot shapes; bolts CONFIRM onto cart mutations |
| `"transferencia"` → `[SUBMIT_DELIVERY_INFO, CONFIRM]` → order placed silently | Same pattern; data + close in one shot inferred where there was no close intent |
| `"mejor una no más"` → `[UPDATE_CART_ITEM, CONFIRM]` → CTA fires, no recap | Cart correction misread as confirmation |
| `"una barracuda y una vittoria"` → `CHAT` (gpt-4o) | Frontier model became overly cautious under conflicting "DO NOT" rules; dropped a clear order |
| `"Si"` after CTA → planner re-emits `SUBMIT_DELIVERY_INFO(echo)` + `CONFIRM` → CTA loop | Planner copies recap card data back as if user typed it |
| `dispatcher dedup machinery` (CONFIRM-with-mutation, blocking-halt, state-aware swap) | Patches stacking on a broken protocol layer |

Six observations about the root cause:

1. **Prompt grew to ~7k tokens** — per-state allowlists, 16 intent definitions, multi-intent rules, anti-patterns, few-shots. Models stop following rules at this length.
2. **Conflicting rules at the seams.** CONFIRMACIÓN says affirmations → CONFIRM. ANTI-PATRONES says mutations → never CONFIRM. Multi-intent says you can do both. Every conflict is a place the model slips.
3. **Custom JSON-intent protocol.** The intent vocabulary (`ADD_TO_CART`, `CONFIRM`, …) is something we hand-rolled and teach the model in-prompt. Frontier models are trained on *tool calling*, not custom JSON. We're working against the grain.
4. **Frontier model regressed.** `gpt-4o` followed instructions *more* literally — became risk-averse, fell back to CHAT on clear orders. More capability didn't help because the prompt was the bottleneck.
5. **The "smart executor" kept growing** — dedup, blocking-halt, state-aware SUBMIT/CONFIRM swap. Each fix layered on the broken core. We were patching, not fixing.
6. **CONFIRM was always a leaky abstraction.** A "let-the-executor-decide" intent. With multi-intent it became *infectious* — the model bolts it onto everything.

## The new architecture

### Today

```
inbound message
  → ConversationManager (orchestration)
      → Router (LLM, picks domain)
          → OrderAgent
              → Planner (LLM, emits JSON intent vocabulary)
              → Executor (Python switch on intent name; allowlist + dedup + state machine)
              → Response Generator (LLM, turns result_kind into prose)
```

### After

```
inbound message
  → ConversationManager (unchanged)
      → Router (unchanged)
          → OrderAgent
              → [B] Deterministic short-circuits (cancel, greeting, CTA button, exact catalog match)
              → [A] Tool-calling agent (LLM with typed tools)
                  └── tools mutate shared session state, return structured results / errors
              → Response Generator (LLM, turns tool results into prose)
```

Three things to notice:
- `ConversationManager` and `Router` are unchanged. The refactor is scoped to *inside* the order agent.
- The planner+executor split disappears. In their place: an LLM that calls typed tools. The "intent" abstraction is gone.
- Session state still lives globally in the DB — every tool reads/writes the same source of truth. The state *machine* (the rules about when transitions happen) lives in the tools' precondition checks.

## Option A — Tool-calling agent (architecture)

### What changes

| Concept today | After A |
|---|---|
| `INTENT_ADD_TO_CART = "ADD_TO_CART"` constant | function `add_to_cart(product_name, quantity, notes)` |
| `execute_order_intent(intent, params)` switch | `tools = [add_to_cart, submit_delivery_info, place_order, view_cart, …]` registry |
| `_extract_intents()` + `_sort_intents_canonical()` + dedup | OpenAI / Anthropic native `tool_calls` list |
| `ALLOWED_INTENTS_BY_STATE` table | each tool checks its preconditions, returns structured error |
| `_recovery_result()` soft fallback | model sees structured error, asks user / picks different tool |
| `pending_disambiguation` state across turns | model remembers conversation, calls `add_to_cart` again with the chosen variant |
| Planner prompt ~7k tokens | persona + tool docstrings, ~1–2k tokens |

### What stays

- All the cart / delivery / order business logic. `add_to_cart`'s body is mostly today's `INTENT_ADD_TO_CART` branch from `execute_order_intent`.
- Database session state: `order_context.items`, `delivery_info`, `state` derived field. Tools read/write this.
- Response generator. Same shape — turns the tool result(s) into prose.
- Catalog matching, promo handling, place_order side effects.
- Operator-tagging, debounce/abort, conversation history persistence — all handler-layer; outside the agent.

### Tool-side responsibilities

Each tool owns:

```python
def add_to_cart(product_name: str, quantity: int = 1, notes: str = "") -> ToolResult:
    """Add a product to the customer's cart. Resolves product_name against the
    catalog. Returns structured cart state on success, error on ambiguity / not found."""
    cart = load_cart()
    try:
        product = resolve_product(product_name)
    except AmbiguousProductError as e:
        return ToolResult(ok=False, error="ambiguous", options=[…])
    cart.add(product, quantity, notes)
    save_cart(cart)
    return ToolResult(ok=True, cart=cart.summary())
```

```python
def place_order() -> ToolResult:
    cart = load_cart()
    delivery = load_delivery_info()
    if not cart.items:
        return ToolResult(ok=False, error="cart_empty")
    if not delivery.is_complete():
        return ToolResult(ok=False, error="missing_delivery", missing=[…])
    order = create_order(cart, delivery)
    return ToolResult(ok=True, order_id=order.id, total=order.total)
```

The model sees the structured error and decides what to do next: ask the user for the missing info, request a different variant, etc. No prose-level state machine in the prompt.

### Tradeoffs

**Pros**
- Aligned with how frontier models are trained — stop fighting the protocol.
- Multi-action is native (`tool_calls` is already a list).
- State validation co-located with code that owns it.
- Failure modes are debuggable in the LangSmith UI: you see exactly which tools were called and what they returned.

**Cons**
- Less explicit control over execution order (model decides).
- Tool-error retries within a turn add latency variance (sometimes 2–3 LLM calls per turn instead of 1).
- Planner unit tests get rewritten. The intent-name-shape tests (`test_extract_intents`, `test_sort_intents_canonical`) become obsolete; tool-side precondition tests replace them.

### Effort

~3–5 days of focused work. The destination is a smaller codebase (the dispatcher, dedup machinery, intent constants delete) but the migration touches a lot of test surface.

## Option B — Deterministic pre-router (optimization)

### What it does

A sequence of cheap Python checks at the top of `OrderAgent.execute()` that bypasses the LLM for known patterns. Each check is independently shippable.

Initial set (most already exist as ad-hoc helpers — formalize as a router):

| Check | Action |
|---|---|
| CTA button payload (`Confirmar pedido` / `Cambiar algo`) | Emit deterministic CONFIRM / CHAT |
| Cancel-verb match (`cancela el pedido`, `anula`, …) | Emit ABANDON_CART / CANCEL_ORDER |
| Pure greeting | Already routed before agent — keep |
| Exact catalog single-product match (`barracuda`, `bimota`) | Emit ADD_TO_CART directly |
| Affirmation-only short message after CTA | Emit CONFIRM |
| Phone regex `\b3\d{9}\b` + address-keyword | Pre-extract delivery info, pass to LLM as already-captured |

### Tradeoffs

**Pros**
- Cheapest path. ~70–80% of traffic short-circuits to zero LLM calls.
- Most predictable. Every short-circuit is reasoned in Python and unit-testable without LLM mocks.
- Latency win is real. Deterministic paths run in milliseconds.

**Cons**
- Each new pattern is a code change, not a prompt edit.
- The router becomes its own complexity surface that can grow without bounds (we already saw the cancel-keyword tuple grow this session).
- Doesn't help on genuinely ambiguous traffic — the residual LLM still has to reason.

### Why B comes after A, not before

If you ship B alone:
- The residual LLM still has the same broken architecture for the cases B can't pre-classify.
- B grows unbounded as you try to compensate for unfixed core failures (`"mejor una no más"` is hard to catch with regex without false positives).
- Two systems to debug — the deterministic router AND the planner.

If you ship A first:
- A 1–2k prompt + tools is enough for most ambiguous traffic. You measure where it still fails.
- B is built **reactively** from data — only the patterns that A keeps slipping on. Stays small.
- One system to reason about. B is a thin pre-routing layer, not a parallel rule engine.

## Migration plan

Five phases. Each ends in a deployable state — no big-bang switchover.

### Phase 0 — Tool API design (1 day)

- Define the `ToolResult` shape (success, error, structured payload).
- Audit current `app/services/order_tools.py` tools. List which already exist as `@tool`-decorated callables vs which are intent-branches in `execute_order_intent` that need to become functions.
- Decide on context passing: `injected_business_context` becomes a context-var or an agent-owned closure, not a tool kwarg.

### Phase 1 — Tool refactor (1–2 days)

- Port each `INTENT_*` branch from `execute_order_intent` into a standalone tool function returning structured `ToolResult`.
- Each tool inlines its own preconditions (replaces the centralized allowlist).
- Preserve all current business logic. No behavior changes from the customer's perspective.
- Existing planner still runs against these tools via the legacy switch — both paths usable in parallel for testing.

### Phase 2 — Tool-calling agent (2 days)

- Build a new `OrderAgent` planner path that uses OpenAI tool-calling with the tool registry.
- Persona prompt: ~500 tokens. No intent vocabulary. No anti-patterns. No state-machine prose.
- Multi-tool-call dispatch: iterate the model's `tool_calls`, run each, feed errors back if the model needs to retry within the turn.
- Behind a per-business feature flag (`use_tool_calling_agent`). Biela in shadow mode first — both planners run, only the legacy emits to the user; we compare tool selections.

### Phase 3 — Eval suite + rollout (1 day + monitoring)

- Run existing eval suite (`tests/evals/`) against tool-calling agent. Expect rewrites; capture them.
- Add new evals for the failure modes we hit this month (CONFIRM bolting, post-CTA echo, etc.) — these should pass cleanly under tool-calling.
- Roll Biela to live mode. Watch LangSmith for tool-call patterns. Compare cost / latency / regression rate vs legacy.
- After a week of stable production, delete the legacy planner code path.

### Phase 4 — Deterministic short-circuits (B, reactive)

- Identify the top 3–5 patterns that the tool-calling agent is paying full LLM cost for unnecessarily (button taps, exact catalog matches, etc.).
- Add deterministic short-circuits at the top of `OrderAgent.execute()` that bypass the LLM for those patterns.
- Each short-circuit is a separate PR. Easy to revert individually if a regression shows up.

### Phase 5 — Customer service agent (later, optional)

The customer_service agent has the same shape (planner JSON intent → executor → response generator). If A pays off for the order agent, port the same pattern. Lower priority because the CS agent has a smaller intent vocabulary and tighter prompt today.

## Out of scope

- **Conversation manager / router architecture.** Unchanged. The order agent is what we're refactoring.
- **Customer service agent.** Phase 5 marks where it could come, not when it must.
- **Booking agent.** Different domain, different concerns.
- **Operator-handoff state model.** Already shipped. Operator turns are tagged `agent_type='operator'` and a state-reset fires when an operator intervened. Tool-calling architecture inherits this.
- **Audit/integrity issues** (silent order cancellations from the admin panel, divergence between operator action and DB state — see `+573247084245` trace). Orthogonal to the order-agent refactor; needs its own work on the admin panel side.

## Open questions

1. **Execution order control.** With tool-calling, the model picks the order. Today we enforce `SUBMIT before ADD_TO_CART` deterministically. Do we need a structural pass to reorder tool_calls before execution, or do we trust the model? Probably trust + measure first.
2. **Tool-error retry budget.** How many times do we let the model retry within a turn after a tool error before we send a generic fallback? OpenAI lets you cap this. Initial: 2.
3. **Disambiguation across turns.** Today we save `pending_disambiguation` in session state because the executor is single-shot. With tool-calling, the *next turn's* model needs the same context. We can either (a) trust the conversation history (model remembers), or (b) keep a lightweight `last_disambiguation_options` field in session state and surface it as part of the prompt. (a) is cleaner; (b) is a fallback if (a) drifts.
4. **Cost.** A single tool-calling LLM call is cheaper than today's planner+response (because the prompt shrinks). Multi-tool-call retries can push cost up on hard turns. Net effect: probably break-even or slightly cheaper. Measure during Phase 2 shadow mode.
5. **Streaming response generation.** Tool-calling supports streaming partial responses while tools execute. Worth a separate spike — not part of the initial migration.

## What success looks like

After Phase 4, when we look at a production turn that broke today's architecture:

- `dos barracudas` → model calls `add_to_cart(items=[BARRACUDA, qty=2])`. No CONFIRM bolting because there's no CONFIRM tool to bolt — closing the order is a separate user action.
- `mejor una no más` → model calls `update_cart_item(BARRACUDA, qty=1)`. Cart recap renders. Customer sees the result.
- `Si` after CTA → singleton tool call to `place_order()`. State checks pass; order placed.
- `transferencia` mid-checkout → `submit_delivery_info(payment_method='transferencia')`. CTA card fires only if all data is now complete.
- `dos coca colas y una limonada` → multi-tool-call: `add_to_cart(coca cola, 2)`, `add_to_cart(limonada, 1)`. The limonada one returns ambiguity; model asks "which limonada variant?" without us writing prompt rules to handle it.

None of those scenarios require a 7k-token prompt or a custom JSON-intent dedup machine.
