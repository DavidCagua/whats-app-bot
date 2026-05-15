# Architecture audit — multi-agent / order agent / CS agent

Brief: harsh, opinionated. Based on 532 messages of real conversations across Tue→Thu 2026-05-05/06/07 plus the per-conversation failure map. The user's framing was "the baseline won't scale for multi-intent" — that framing is correct and this document explains why.

---

## Verdict

The order agent's architecture is **not wrong in shape, but wrong in contract**. The shape — Planner LLM → Executor (state machine) → Tools → Response LLM — is the right shape for a 2026 production bot. But every contract between the layers is loose, ambiguous, or defended only by prompt engineering. That's why the same bug class re-appears under different prompts and gets patched 4 times in a week (`8ef5496`, `7ec4aa2`, `4435c40`, `8ef5496` again).

You don't need to swap the architecture. You need to **harden the contracts**, **move state-machine decisions out of the LLM**, and **add a deterministic extraction prepass**. The CONFIRM-dedup hack is a symptom of one specific architectural mistake (the planner emits transitions instead of desires) — fix that one mistake and a third of your patches go away.

The CS agent has the same shape problem at smaller scale, plus its own specific issue: it's used as a fallback for everything the order agent doesn't understand, instead of being a peer with a defined responsibility. That's not a refactor, that's a re-scoping.

The router is fine. The dispatcher is fine. The conversation manager is fine *modulo* the operator-tagging schema gap that you've already started fixing.

---

## The 8 architectural sins, ranked by leverage

### Sin 1 — The planner emits transitions, not desires (THE multi-intent problem)

**Evidence:** W-8 (+573134700128) "Ahí está perfecto gracias" dropped because the planner had to choose between confirming and continuing. Th-5 (+573242739292) order placed without the burger because the planner's multi-intent array dropped the burger ADD_TO_CART. The deterministic CONFIRM dedup hack you wrote yesterday — that's the architecture pushing back at you.

**What's wrong:** the planner LLM emits an array like `[ADD_TO_CART(RAMONA), CONFIRM]`. The executor runs them in order. But the LLM doesn't know what state the system will be in *after* `ADD_TO_CART` runs — it predicts "the user wants to confirm" based on the message, not based on the system state. So when the user says "ramona y confirmamos", the LLM is correct that both intents exist, but it has no business deciding the *order* or whether they should both fire in one turn.

**Why it can't scale:** every new intent you add multiplies the combinatorial space. You'll keep finding {A, B} pairs where the LLM emits both but the state machine can only honor one. Your dedup hack handles {cart-mutation, CONFIRM}. The next pair will be {ADD_TO_CART, REMOVE_FROM_CART} (user says "una ramona, sin la mexicana que pedí antes"). Then {ADDRESS_UPDATE, PLACE_ORDER} (user updates address while confirming). You'll keep stripping things deterministically.

**Fix:** split the planner into two phases:
- **Phase 1 (LLM):** intent extraction. Output an unordered set of *desires* with no commitment. `{add_to_cart: [RAMONA], wants_to_confirm: true}`.
- **Phase 2 (deterministic):** intent scheduling. Read current state + cart + the desires. Produce the ordered transition sequence. `[ADD_TO_CART(RAMONA)] → recompute state → [CONFIRM]` if state allows; else `[ADD_TO_CART(RAMONA)]` only.

Phase 2 is where your business rules live as code, not prompt. The CONFIRM dedup goes away because the scheduler simply doesn't schedule PLACE_ORDER from a non-READY state.

**This is the single highest-leverage refactor in the codebase.** Estimated effort: 1-2 days. Estimated patches it kills: 5+ that you've already shipped, plus the entire combinatorial pile that was coming.

---

### Sin 2 — No deterministic extraction prepass

**Evidence:** W-13 (+573015349690) sent product + name + address + phone + payment in one message. Bot extracted only the product. Th-5 (+573242739292) same shape; bot extracted Coke + dropped the burger ask. W-8 (+573134700128) sent compact info; bot couldn't merge it into the cart. This is your "real life copy-paste" pain.

**What's wrong:** the planner LLM is being asked to do natural-language understanding *and* slot extraction *and* intent classification *and* state-machine transitions, all in one prompt. That's four jobs. NLU and slot extraction are deterministic problems with established solutions (NER, regex with confidence, dedicated small models). Putting them in the same LLM call as intent classification means the LLM has to "remember" the customer gave their address while also "deciding" whether to add a product.

**Why it can't scale:** the more text the user sends, the more the LLM has to track. Multi-line messages are common in WhatsApp (people copy-paste their address + phone routinely). The planner will keep dropping fields under load.

**Fix:** add a prepass module before the planner:
- Run a phone regex (`^\+?57?3\d{9}$` or similar) → if found, set `extracted.phone`.
- Run an address heuristic (looks for street tokens: cra, calle, transversal, autopista, manzana, casa, apto, edificio, torre) → set `extracted.address_text`.
- Run a name heuristic (proper-cased pair of words at start of line not matching catalog/category tokens) → set `extracted.name`.
- Run a payment-method classifier (literal substrings: `efectivo`, `transferencia`, `nequi`, `daviplata`) → set `extracted.payment_method`.

These pre-extracted fields become part of the planner's input as **structured context, not text the LLM has to re-parse**. The planner now only has to decide intent + product, not also do entity extraction.

**Estimated effort:** half a day. **Patches it kills:** the entire "user copy-pasted info but bot only extracted the product" class.

---

### Sin 3 — Tools throw instead of returning Results

**Evidence:** `add_to_cart` raises `AmbiguousProductError` / `ProductNotFoundError` (LangSmith errors all week). Some are caught and become user-friendly messages; some leak as stack traces. T-4 (dev test) added LA VUELTA + MANHATTAN as phantom products because *no exception fired* and the tool just inserted them.

**What's wrong:** tools are infrastructure. Throwing exceptions for ambiguity is treating expected flow as a bug. The result is:
- The planner has to catch errors via try/except in glue code, with no schema for the error.
- The tool's success path doesn't validate that the resolved product is real — if no exception, the tool inserts whatever name was passed in.
- "AmbiguousProductError" appears in logs as if something broke, polluting your error monitoring.

**Why it can't scale:** every new tool you add has to invent its own exception types and catch sites. There's no uniform error contract.

**Fix:** every tool returns a typed result. Pseudocode:
```python
@dataclass
class ToolResult:
    ok: bool
    payload: dict | None    # success data
    error: ErrorKind | None # AMBIGUOUS, NOT_FOUND, INVALID_STATE, ...
    candidates: list | None # for AMBIGUOUS
    hint: str | None        # for the planner to render
```

Tools refuse to write when `ok=False`. `add_to_cart` literally cannot insert a phantom product because the resolver returns `ok=False, error=NOT_FOUND, candidates=[...similar names...]` and the tool short-circuits before any DB write.

**Estimated effort:** 2 days (mechanical refactor across `order_tools.py`, `customer_service_flow.py`, `calendar_tools.py`). **Patches it kills:** all phantom-product cases, all "AmbiguousProductError stack trace" log noise.

---

### Sin 4 — State machine isn't authoritative

**Evidence:** W-13 (+573015349690) had cart mutated AFTER PLACE_ORDER because the customer complained about a missing item and the planner emitted REMOVE_FROM_CART. The state was PLACED. The tool didn't care.

**What's wrong:** `order_status_machine.py` defines states but doesn't enforce them on tool calls. The state is descriptive, not prescriptive. So your bug-prevention story relies on the planner *choosing* not to emit a wrong-state intent — which it will get wrong some percentage of the time.

**Why it can't scale:** every new state you add (e.g., `IN_DELIVERY`, `RATING_PENDING`) multiplies the planner's awareness burden. The architecture is one prompt-tweak away from "ah, the planner forgot the new state existed" bugs.

**Fix:** every tool declares the states it accepts:
```python
@accepts_state(ORDER_STATE_ORDERING, ORDER_STATE_COLLECTING_DELIVERY)
def add_to_cart(...) -> ToolResult: ...

@accepts_state(ORDER_STATE_READY_TO_PLACE)
def place_order(...) -> ToolResult: ...
```

The executor reads the current state from `conversation_sessions.order_context` and rejects out-of-state tool calls with `ToolResult(ok=False, error=INVALID_STATE)`. The planner gets a typed result it can react to ("ah, can't add to cart in PLACED state — the customer must want to file a complaint").

**Estimated effort:** half a day. **Patches it kills:** post-PLACE_ORDER cart mutation, ghost cancels on placed orders, the "customer complaint becomes cart edit" class.

---

### Sin 5 — Response generator is allowed to author facts

**Evidence:** W-2, W-8, Th-3 all hallucinated "5 minutos" or "15-20 minutos" ETAs. T-4 dev test had bot say "no tengo precios" after order placed. W-8 hallucinated "no manejamos cambio" + "tarjeta" payment method (neither is true). The response LLM is generating numerical/factual content with no grounding.

**What's wrong:** the second LLM (response generator) is supposed to render the executor's typed result into Spanish. In practice it's doing more than that — it's filling in details that the executor didn't return. When the executor says "order_status=PLACED, eta_unknown", the LLM helpfully writes "5 minutos" because it sounds nice.

**Why it can't scale:** any factual claim the LLM can phrase eloquently, it will phrase. You'll never finish playing whack-a-mole with hallucinations. And every hallucinated number is a customer-trust event.

**Fix:** split customer-facing strings into two classes:
- **Factual strings (numbers, IDs, addresses, ETAs, totals, payment methods, hours):** templated by the executor, rendered verbatim by the response generator. The response LLM gets `<<RENDER_VERBATIM>>$35.000<</RENDER_VERBATIM>>` markers in its output spec and is *forbidden* from rephrasing them.
- **Soft strings (greetings, transitions, recovery):** free-form LLM.

If the executor doesn't have an ETA, the response is "déjame verificar y te aviso" — period. The LLM cannot invent.

**Estimated effort:** 1-2 days. **Patches it kills:** all ETA hallucinations, fake business policies, fake payment methods, wrong totals.

---

### Sin 6 — Debouncer is dumb about typing bursts

**Evidence:** Th-5 (+573242739292) sent 5 messages in 18 seconds; the planner ran on a partial message set and produced an order without the burger.

**What's wrong:** `debounce.py` has the right primitives (abort, coalesce, wait window) but the wait window is fixed-short. It doesn't react to the user *still typing*.

**Why it can't scale:** WhatsApp users in Latin America send rapid bursts. This isn't an edge case, it's the median.

**Fix:** make the wait adaptive. Pure timing logic, no LLM:
- Initial wait: current short value.
- If a new message arrives during the wait, reset the timer with multiplicative backoff (`wait *= 1.4`, capped at 8s).
- If two messages arrive within sub-second cadence, switch to "typing storm" mode: require 3 seconds of silence before firing.
- Hard cap at 10s overall regardless.

**Estimated effort:** half a day. **Patches it kills:** the entire "planner ran while user was still typing" class.

---

### Sin 7 — Cancel keywords use substring matching

**Evidence:** Th-6 (+573104078032) "cancelo al domiciliario" (Colombian Spanish: "I'll *pay* the courier") triggered ORDER_CANCEL because `cancel_keywords.py` matched the substring `cancelo`.

**What's wrong:** `cancel_keywords.py` is a list of strings. Substring match. Doesn't know Colombian Spanish ("cancelar" = pay) or context (cancelar al domiciliario ≠ cancelar el pedido).

**Why it can't scale:** every regional variant adds a new false positive. You can't blacklist your way out of natural language.

**Fix:** the cancel-keywords check is the wrong abstraction. Cancel detection belongs in the planner LLM with a clear instruction ("CANCEL_ORDER only when the user explicitly says cancelar el pedido / la orden — never when 'cancelar' is used in another sense"). For deterministic protection, gate destructive intents on state ("you can only cancel an existing PLACED order, and only with explicit confirmation"). Keep the keyword file as a *signal* (raises a flag), not as a *verdict*.

**Estimated effort:** half a day if you remove the substring matcher; some prompt iteration for the planner. **Patches it kills:** regional-Spanish false positives.

---

### Sin 8 — CS agent is the dumping ground

**Evidence:** Anything the order agent doesn't understand falls through to CS. CS then has to handle promo queries, hours queries, menu links, payment-detail questions, complaints, and post-delivery issues — without a clear scope. W-8's "no manejamos cambio" hallucination is in CS. T-12's "Puedes contactarnos al +573177000722" instead of Nequi info is in CS. W-13's "se cobra aparte" defending a charge instead of handling a complaint is in CS.

**What's wrong:** CS doesn't have its own state machine or tools — it's mostly a freeform LLM with a system prompt. So it inherits every "bot says something weird" failure mode without any of the order agent's structural defenses.

**Why it can't scale:** as you add restaurants and verticals, the CS agent's prompt grows linearly. It already has rules for hours, menu, payment, promotions, and complaints. Each new feature is a new paragraph in the prompt. This is Conway's law catching up.

**Fix:** CS should be a peer of the order agent with the same architectural shape (Planner → Executor → Tools → Response). Specifically:
- Tools for `get_hours()`, `get_menu_url()`, `get_payment_details()`, `get_promotions()`, `open_complaint_ticket()`. Each returns a typed result from `business.settings`.
- Planner emits `INTENT: ASK_HOURS / ASK_MENU / ...`.
- Response generator renders the tool result.

This kills the inconsistency where the same prompt produces different answers (W-3 vs W-4). It also makes CS testable.

**Estimated effort:** 2-3 days. **Patches it kills:** all CS hallucinations, all "inconsistent answer to same question" cases.

---

## Should the order agent be swapped?

The user asked this directly. Three architectures could be candidates:

**Option A — Keep the current shape, fix the contracts (RECOMMENDED).** Planner → Executor → Tools → Response. Apply Sins 1-7 above. This is a 2-week refactor with measurable wins per day. No big-bang rewrite, no breakage window.

**Option B — Tool-using agent loop (Anthropic / OpenAI native pattern).** Replace the explicit planner with a single LLM that calls tools in a loop until it decides it's done. The state lives in the tool returns. Multi-intent is "the LLM keeps calling tools." This is what Claude / GPT agents do natively and what frameworks like Vercel AI SDK / LangGraph promote in 2026.

  - Pros: handles multi-intent naturally; simpler prompt; matches modern agent patterns.
  - Cons: cost (multiple LLM calls per turn), latency (each tool call is a roundtrip), less determinism, harder to debug, harder to enforce business rules.
  - For a *delivery bot* where the happy path is well-known and latency matters: this is the wrong choice. You'd be paying for flexibility you don't need.

**Option C — Mostly deterministic flow with LLM only at edges (what Domino's / Uber Eats actually use).** The state machine drives most of the conversation. LLM is used only for: (1) NLU at message ingress, (2) tone wrapping at egress. The middle is code.

  - Pros: most reliable, fastest, cheapest.
  - Cons: very rigid; struggles with messy multi-intent; bad UX when the user goes off-script.
  - For a small barbershop / restaurant where you control the menu and customers are repeat users: this is fine.
  - For a multi-tenant platform with diverse merchants: this constrains the product roadmap.

**Recommendation: Option A.** The current shape is right; you've correctly identified that the contracts are wrong. Don't swap the engine while patching the gearbox. Ship Sins 1-4 in the next 2 weeks; revisit whether you still feel the architecture pushing back. If you do — only then consider Option B for specific failure modes (e.g., CS agent could go fully agentic since it's lower-stakes).

---

## Refactor priority order (ship in this sequence)

The order matters because each step makes the next safer.

1. **Tools return Results, not exceptions** (Sin 3). Mechanical, testable, no behavior change — this is the safety net that lets later refactors fail safely.
2. **State machine enforced on tool calls** (Sin 4). Stops post-PLACE_ORDER cart mutations, gives the planner typed errors to react to.
3. **Deterministic extraction prepass** (Sin 2). Strips entity extraction out of the planner; immediately fixes copy-paste cases.
4. **Phase 1 / Phase 2 planner split** (Sin 1). The big one. CONFIRM dedup hack disappears; multi-intent becomes scheduling, not prompting.
5. **Adaptive debouncer** (Sin 6). Easy win, removes the typing-storm class.
6. **Lock down response generator for facts** (Sin 5). Kills hallucinations.
7. **Remove substring cancel matching** (Sin 7). Small.
8. **CS agent gets its own tool layer** (Sin 8). Bigger; do after the order agent is stable.

**By end of week 2, you should have shipped 1-5.** That covers the multi-intent scaling problem you're worried about. After that, 6-8 are quality polishing.

---

## What this audit doesn't say

- It doesn't say to swap the LLM provider (you're using gpt-4o-mini for cost; if you want to upgrade, do it after the architecture is solid — better LLM on bad architecture = more expensive bad architecture).
- It doesn't say to add LangGraph or another orchestration framework yet. They become useful when you have many agents with complex flows. Right now you have 3 agents and the orchestration is straightforward — adding a framework now is overhead, not leverage.
- It doesn't say to abandon LangChain. LangChain's tool-calling primitive is fine. The issue is *what your tools return*, not what's calling them.
- It doesn't say to rebuild the conversation manager. `session_state_service` + `conversation_sessions.order_context` is the right shape. The data model is good. The contract on top of it is what's loose.

---

## Operator workflow note (orthogonal but blocking)

The architecture audit doesn't fully cover this, but flagging: **3 of the 4 most damaging incidents this week (#3ACB4460 phantom paste, +573104078032 wrong-ID paste, #BFCCE966 burger amendment) involved operators copy-pasting bot templates into the WhatsApp thread.** You can fix every architectural sin above and still see these because operators have no in-product path. `483b36b` (Crear pedido) is the start; it needs a sibling `Amend pedido` and `Reactivate pedido` to be complete.

Operators *want* to do the right thing. They reach for copy-paste because there's no alternative.

---

## TL;DR

- Architecture is right-shape, wrong-contract.
- Multi-intent fails because the planner emits transitions instead of desires. Split into Phase 1 (LLM extracts desires) + Phase 2 (deterministic scheduler). Single biggest leverage point.
- Don't swap the engine. Refactor in 8 ordered steps. Multi-intent scaling problem is solved by step 4.
- CS agent needs the same shape as the order agent, but smaller.
- Operators need real admin tools; otherwise they keep pasting bot templates.
