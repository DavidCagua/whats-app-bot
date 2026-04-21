# Multi-Agent Architecture Plan

**Status**: Proposed — not yet implemented
**Branch**: `feature/multi-agent-architecture`
**Author notes**: David C. — drafted 2026-04-18 in collaboration with Claude

## Goal

Introduce a scalable multi-agent architecture so the bot can handle multiple distinct domains (orders, customer support / post-venta, bookings, future marketing) without bloating the order agent's prompt or coupling concerns.

The current `conversation_manager` picks a single agent per turn via `business_agent_service.get_enabled_agents()` + the `conversation_primary_agent` setting. This works for single-agent businesses but does not support:

1. **Intent-based routing** across multiple active agents.
2. **Mixed-intent turns** where the user expresses two intents in one message (e.g. "dame una barracuda y a qué hora abren mañana").
3. **Cross-agent conversations within a session** — user orders, then asks about hours, then continues ordering. Cart state must survive the detour.

## Non-goals

- Mid-turn agent-to-agent handoffs (e.g. LangGraph-style `handoff`). Not needed at our scale.
- Shared tools across agents. Strict separation from day one.
- LangGraph / OpenAI Swarm migration. Overkill for turn-based dispatch; can revisit later.
- Replacing agent-level response generators with a single general one (see "Design principle #3").

## Design principles

### 1. Stateless routing, stateful domain

The router has no memory. Every turn, it classifies the message and picks agent(s) based solely on the user's message + global signals (customer name, business context). It does NOT use `active_agents` or last-turn history to stick to an agent — that breaks the "user returns hours later with new intent" case.

### 2. Agent-owned session slots

Each agent owns a namespaced slot in the session:

| Agent | Session slot it owns |
|-------|---------------------|
| `order` | `order_context` (cart, order_state, pending_disambiguation, delivery_info) |
| `support` | `support_context` (last_lookup_order_id, last_complaint_id) |
| `booking` | `booking_context` |
| `marketing` | `marketing_context` (future) |

**Rules**:
- An agent only **writes** to its own slot.
- Agents may **read** from other slots for context (e.g. support reads `order_context.items` to say "veo que tienes barracuda pendiente...").
- Shared facts that multiple agents need (customer name, VIP status, last order placed) live in a future `conversation_context` namespace with a narrow API — not raw dict mutation.

### 3. Two-level response generation

- **Agent-level response generators** stay. Each agent produces a domain-perfect reply from structured execution results. The order agent's ~500-line `_build_response_prompt` keeps its knowledge of `result_kind` branches.
- **Turn-level composer** is new. It only runs when >1 agent produced output in the same turn. It takes pre-rendered text from each agent and stitches them naturally. It does NOT regenerate domain content.

Single-intent turns (90%+ of traffic) skip the composer entirely — zero new latency for the common case.

### 4. Strict separation, no shared tools

The support agent does not use `add_to_cart`. The order agent does not use `get_order_status`. If a cross-domain capability is needed, the **router** splits the turn and dispatches to both agents.

Trade-off: mixed-intent turns pay 2 full agent pipelines (~3.5-4.5s) instead of one agent calling a shared tool (~2s). Acceptable because mixed-intent turns are rare.

## Architecture

```
┌────────────────────────────────────────────────────┐
│  Router / Intent Decomposer                        │
│  Input: user message, business_context, name       │
│  Output: [(agent_type, segment), ...]              │
│  LLM: gpt-4o-mini, ~300ms                          │
├────────────────────────────────────────────────────┤
│  Agent Dispatcher                                  │
│  For each (agent, segment):                        │
│    1. Load session (turn_cache)                    │
│    2. Call agent.execute(segment, ...)             │
│    3. Persist agent's state_update                 │
│    4. Accumulate response text                     │
├────────────────────────────────────────────────────┤
│  Agents (strict separation, agent-owned slots)     │
│                                                    │
│    order agent                                     │
│      planner → executor → response generator       │
│      writes: order_context                         │
│                                                    │
│    support agent (NEW)                             │
│      planner → executor → response generator       │
│      writes: support_context (mostly read-only)    │
│                                                    │
│    booking agent (existing, already separate)      │
│    marketing agent (future)                        │
├────────────────────────────────────────────────────┤
│  Response Composer (skip if 1 agent output)        │
│  Input: ordered list of agent reply strings        │
│  Output: single merged Spanish reply               │
│  LLM: gpt-4o-mini, ~150ms                          │
└────────────────────────────────────────────────────┘
```

### Router prompt shape

```
Eres el router. Tu trabajo es leer el mensaje del usuario y decidir qué
agente(s) deben responder.

Agentes disponibles:
- order:   hace pedidos, ver menú, agregar al carrito, checkout
- support: estado de pedido, horarios, dirección, quejas
- booking: reservar turno / cita (cuando esté habilitado)

Responde en JSON:
  { "segments": [ { "agent": "order", "text": "dame una barracuda" }, ... ] }

Si hay un solo intent, un solo segment. Si hay varios, segmenta el mensaje.
```

### Agent contract

Each agent's `execute()` method:
- Accepts: `message_body`, `wa_id`, `name`, `business_context`, `conversation_history`, `session`, `message_id`, `stale_turn`, `abort_key`, `**kwargs`.
- Returns: `{ "agent_type": str, "message": str, "state_update": dict }`.
- `state_update` only touches the agent's own namespaced slot. The dispatcher merges.

### Mixed-intent execution order

When the router returns multiple segments, the dispatcher runs them **sequentially** in the order returned. Rationale: if the order agent mutates the cart and the support agent references it, the support agent needs to see post-mutation state.

Parallelism is a later optimization. Ship sequential first, measure, optimize if avg mixed-intent latency becomes a problem.

## Implementation phases

### Phase 0: Intent audit + order agent slim-down

**Scope**: Before building the router (Phase 1), audit which of the order agent's current intents actually belong there. Move non-order intents out so the order agent ends up strictly about cart mutations + checkout.

**Current order-agent intents** (from `order_flow.py`):

| Intent | Belongs in | Rationale |
|--------|-----------|-----------|
| `GREET` | **Router / business greeting service** | Already covered in Phase 1. Agent-neutral. |
| `GET_MENU_CATEGORIES` | **Catalog (shared read-only capability)** | Reading the menu isn't an order concern; support would also use it ("qué tienes?" is a discovery question, not a cart action). Belongs in a catalog service the router can call directly OR a `catalog` sub-domain shared across agents as a read-only capability. |
| `LIST_PRODUCTS` | **Catalog** | Same reasoning. Pure read. |
| `SEARCH_PRODUCTS` | **Catalog** | Same reasoning. The product search pipeline (`product_search.py`) is already framework-agnostic — just expose it through a router-callable path. |
| `GET_PRODUCT` | **Catalog** | Same reasoning. |
| `VIEW_CART` | Order (keep) | Reads order state, but the cart IS the order domain. Keep. |
| `ADD_TO_CART` | Order (keep) | Pure cart mutation. |
| `UPDATE_CART_ITEM` | Order (keep) | Pure cart mutation. |
| `REMOVE_FROM_CART` | Order (keep) | Pure cart mutation. |
| `PROCEED_TO_CHECKOUT` | Order (keep) | State transition inside order flow. |
| `GET_CUSTOMER_INFO` | **Customer service (shared)** | Pure read of customer profile. Support and marketing will want this too. |
| `SUBMIT_DELIVERY_INFO` | Order (keep) | Writes order-scoped delivery info. |
| `PLACE_ORDER` | Order (keep) | The terminal order action. |
| `CONFIRM` | Order (keep) | Semantic — executor resolves to concrete action in current order state. |
| `CHAT` | Router fallback | Not an order intent; it's "nothing matched." Route to a default reply or an "I didn't understand" fallback. |

**Extraction strategy**:

The **catalog** is the biggest extraction. Three options:

- **Option A (chosen)**: Catalog becomes a **shared read-only capability** available to the router directly AND to any agent that needs it. The router can answer menu queries without dispatching to an agent at all (like greeting). Same service backs a potential future "menu display" feature in the admin console.

- Option B: Create a `catalog_agent` whose sole job is menu/search. Adds one more agent for one concern. Over-engineered for today.

- Option C: Leave menu intents in the order agent. Keeps order agent bloated; support agent would duplicate to answer "qué bebidas tienen?"

Option A keeps the separation clean without adding an agent.

**Files**:
- `app/services/catalog_service.py` (new) — thin wrapper over existing `catalog_cache` + `product_search`. Exposes:
  ```python
  list_categories(business_id) -> List[str]
  list_products(business_id, category=None) -> List[dict]
  search_products(business_id, query, unique=False) -> List[dict]
  get_product(business_id, product_id | name) -> dict
  ```
- `app/orchestration/order_flow.py` — remove `INTENT_GET_MENU_CATEGORIES`, `INTENT_LIST_PRODUCTS`, `INTENT_SEARCH_PRODUCTS`, `INTENT_GET_PRODUCT`, `INTENT_GET_CUSTOMER_INFO`, `INTENT_GREET`, `INTENT_CHAT` and their executor branches. Keep the `RESULT_KIND_*` enums corresponding to kept intents only.
- `app/agents/order_agent.py` — remove response-generator branches for extracted `result_kind`s.
- `app/agents/order_agent.py` PLANNER_SYSTEM_TEMPLATE — slim from 15 intents to 8: `ADD_TO_CART, VIEW_CART, UPDATE_CART_ITEM, REMOVE_FROM_CART, PROCEED_TO_CHECKOUT, SUBMIT_DELIVERY_INFO, PLACE_ORDER, CONFIRM`. Remove the massive "Reglas de menú y búsqueda" block entirely — the order planner no longer classifies those.

**Planner prompt refactoring**:

The current `PLANNER_SYSTEM_TEMPLATE` is ~80 lines and encodes rules for intents we're extracting. After extraction it should be:
- One block describing the 8 remaining cart/checkout intents.
- The REFERENCIA PRONOMINAL rule stays (pronouns referring to products from conversation history still apply when adding to cart).
- The `<SENDER>` marker rule stays.
- Remove: menu-category rules, search-vs-list rules, "qué tiene cada una" pluralization rule, ingredient-search rules — all of these are now catalog-service responsibility.
- Estimated size reduction: ~80 lines → ~35 lines. Major latency win on planner token cost + clarity.

**Router responsibilities for extracted intents**:

The router classifies a message into one of:
- `greeting` → business_greeting service (already in Phase 1).
- `catalog` → `catalog_service` called directly, response rendered by a small templated formatter or a `catalog_response_generator` LLM call (~300ms) that formats the results naturally. No agent dispatch.
- `order` → dispatch to order agent with the slim intent set.
- `support` → dispatch to support agent (Phase 2).
- `booking` → dispatch to booking agent (future).
- `marketing` → dispatch to marketing agent (future).
- `chat_fallback` → a generic "no entendí" reply with a suggestion prompt.

The router prompt stays short because it only picks between domains, not intents.

**Tests**:
- Unit: each catalog service method returns expected data from fixtures.
- Regression: previously order-agent-classified menu queries now flow through catalog service and produce equivalent outputs (the user-facing reply should be byte-equivalent or better).
- All existing evals (`tests/evals/test_capability.py`, `test_regression.py`) must still pass. Any failure is a signal the extraction dropped behavior.

**Success criteria**: Order agent's `PLANNER_SYSTEM_TEMPLATE` shrinks by ~50%. Menu/search queries route through the catalog service without touching the order agent. Existing eval suite green.

---

### Phase 1: Router refactor + greeting extraction

**Scope**: Replace the current static agent selection in `conversation_manager.process()` with an LLM-based router. Extract greeting handling from the order agent so it becomes a router-level capability, not an agent intent.

**Files**:
- `app/orchestration/conversation_manager.py` — extract routing into a new `Router` class.
- `app/orchestration/router.py` (new) — LLM classifier returning `[(agent, segment), ...]`.
- `app/services/business_greeting.py` (new) — `get_greeting(business_context, customer_name) -> str` returns the templated greeting (business name, hours, menu URL, emojis). No LLM.
- `app/agents/order_agent.py` — remove `INTENT_GREET` branch from `execute()`, remove `GREET` from `PLANNER_SYSTEM_TEMPLATE`.
- `app/agents/base_agent.py` — no change (already accepts `**kwargs`).

**Greeting rules** (in router, executed before LLM classification):
- **Pure greeting fast-path**: message is only "hola", "buenas", "buen día", "buenos días", "hey", etc. with no product name, no question mark, no cart action, no non-greeting content → router returns `business_greeting.get_greeting(...)` directly, skips agent dispatch entirely.
- **Greeting + intent** ("hola, quiero una barracuda"): router strips the greeting token mentally and dispatches the rest to the right agent. The agent's response generator handles warmth naturally. No explicit greeting string returned; tone flows through the agent's reply.

**Why extract greeting**:
- Greeting touches no agent state (no cart mutation, no `order_state` change) — it doesn't belong in any single agent.
- The greeting references business-level facts (hours, menu URL) that support and marketing will also need. Keeping it in order agent duplicates knowledge.
- Pure greetings shouldn't force router classification into one agent; they're agent-neutral.
- Removes ~25 lines from order agent, simplifies the planner prompt.

**Tests**:
- Pure greetings ("hola") return the template without invoking any agent (assert no LLM called beyond the optional fast-path).
- Greeting + product ("hola quiero una barracuda") skips the greeting fast-path, dispatches to order agent, cart is updated.
- Greeting + support intent ("hola a qué hora abren") skips the fast-path, dispatches to support agent.
- Single-intent cases route correctly.
- Mixed-intent ("dame X y a qué hora abren") splits into 2 segments.
- Router LLM failure falls back to primary agent.

**Success criteria**: Existing single-agent behavior is byte-identical except the greeting no longer runs through the order agent's planner. New multi-segment outputs only possible once Phase 2 lands.

### Phase 2: Support agent (read-only)

**Scope**: Create `app/agents/support_agent.py` with 3 intents:
- `GET_ORDER_STATUS` — look up the user's latest order, report status.
- `GET_BUSINESS_INFO` — hours, address, phone, menu URL (from `business.settings`).
- `LOG_COMPLAINT` — save a free-text complaint to a new table or existing field.

**Files**:
- `app/agents/support_agent.py` (new) — same planner → executor → response-gen pattern as order agent.
- `app/services/support_tools.py` (new) — three tools above.
- `app/orchestration/support_flow.py` (new) — parallel to `order_flow.py`.
- Migration: add `complaints` table OR add `complaint_text` column to `orders`. Decide with owner.

**Tests**:
- Order status lookup returns correct info when order exists.
- Order status lookup handles "no order found" gracefully.
- Business info queries work across all settings fields.
- Complaint log persists and is retrievable.

**Success criteria**: Support agent can be enabled in `business_agents` table and handles its 3 intents end-to-end in isolation.

### Phase 3: Dispatcher + composer

**Scope**: Wire the router's multi-segment output to sequential dispatch, and add the response composer.

**Files**:
- `app/orchestration/agent_executor.py` — becomes `dispatch_agents([(agent, segment), ...])` instead of single `execute_agent`.
- `app/orchestration/response_composer.py` (new) — LLM call that merges N agent replies.
- `app/orchestration/conversation_manager.py` — orchestrates router → dispatcher → composer.

**Tests**:
- Single-agent turn: composer is skipped, reply is agent's output verbatim.
- Two-agent turn: both agents run in order, composer produces coherent merged reply.
- State isolation: order agent's cart mutation is visible to support agent running after.
- Failure in one agent does not prevent the other from replying.

**Success criteria**: User says "dame una barracuda y a qué hora abren mañana" → gets ONE reply that confirms the cart addition AND answers the hours question.

### Phase 4: Enable support for Biela

**Scope**: Flip the feature on for the first customer.

- Add `support` to `business_agents` for Biela's business_id.
- Set `business.settings.conversation_primary_agent = "order"` (already is).
- Ensure business info fields are populated (`hours`, `address`, `phone`).
- Monitor LangSmith traces for router accuracy on real traffic.

**Success criteria**: 1 week of production traffic with >95% correct routing, no regressions on order flow.

## Open questions (discuss with owner)

1. **Order statuses**: are `pending / completed / cancelled` enough, or do we need `preparing / en_camino / delivered`? Determines whether support can give meaningful status replies beyond "tu pedido está pendiente."
2. **Complaints workflow**: does the owner want to receive complaint notifications (Slack, admin console flag), or just have them logged for later review?
3. **Modification requests**: should support allow the customer to modify an order (add/remove items) before it's marked `preparing`? Or is modification always a "call us directly" case?
4. **Business info**: where do hours live today? `business.settings.hours` needs a structured format the support agent can query (e.g. per-day-of-week dict).

## Risks

1. **Router misclassification**: if the router picks `support` when the user meant to order, the user gets a business-info answer instead of their cart updated. Mitigation: LangSmith dashboards on router intent, fast iteration on the prompt, fallback to primary agent on low-confidence outputs.
2. **Latency on mixed-intent turns**: ~4.5s vs ~2s. Mitigation: keep mixed-intent turns rare (they naturally are), add parallel dispatch later if needed.
3. **State corruption if agents violate slot ownership**: the convention (each agent writes only its slot) is not enforced by code. Mitigation: add runtime check in the dispatcher that `state_update` keys are in the agent's allowed slot list. Flag violations as warnings.
4. **Response composer hallucination**: composer could subtly alter facts from the agent outputs. Mitigation: keep composer prompt narrow ("merge these replies, do not add facts, do not change numbers"), low temperature, test with adversarial cases.

## Rollback plan

Phase 1 (router) and Phase 3 (dispatcher) are behind a feature flag. If the router misbehaves in production, flip the flag to fall back to the current static-agent selection. Phase 2 (support agent) is additive — disabling it reverts to order-only behavior.

## Testing strategy

### Reorganization: by component, not by file type

Current layout (`tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/evals/`) groups by test *type*. That works for one agent but breaks down with multiple: `test_order_agent.py` + `test_support_agent.py` + `test_booking_agent.py` in the same `unit/` folder, no easy way to run "all tests for the router" or "all tests for support."

New layout groups by **architectural component**:

```
tests/
├── router/                    # Router classification + decomposition
│   ├── unit/                  # Classifier logic on mocked LLM
│   ├── integration/           # Router with real LLM, golden inputs
│   └── evals/                 # Routing accuracy on labeled dataset
├── agents/
│   ├── order/
│   │   ├── unit/              # Planner, executor, response gen in isolation
│   │   ├── integration/       # Full agent pipeline with mocked LLM
│   │   └── evals/             # Order-specific capability + regression
│   ├── support/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── evals/
│   ├── booking/
│   │   └── ...
│   └── marketing/
│       └── ...
├── services/                  # Shared capabilities (catalog, business_greeting, customer_memory)
│   ├── catalog/
│   ├── business_greeting/
│   └── customer_memory/
├── infrastructure/            # Non-domain concerns
│   ├── debounce/
│   ├── turn_lock/
│   ├── turn_cache/
│   └── abort/
├── e2e/                       # Full-stack scenarios
│   ├── single_intent/
│   ├── mixed_intent/
│   ├── mid_turn_handoff/
│   └── cross_domain_session/  # User switches domains across turns
└── conftest.py
```

### Test types per component

Each component folder has three subfolders with a consistent contract:

- **`unit/`** — no LLM calls, no DB, pure logic. Mocked dependencies. Fast (<1s per test). Runs on every commit.
- **`integration/`** — real LLM calls (cached VCR cassettes where possible), real DB (test schema). Slower (~2-10s per test). Runs on PR.
- **`evals/`** — measures *behavioral quality* on labeled inputs. Uses `agentevals` / `langsmith`. Reports pass-rate / accuracy rather than pass/fail per case. Runs nightly + on changes to prompts.

### Evals: agent-specific + router-specific

Evals today are in `tests/evals/test_capability.py` + `test_regression.py`, mixing order-agent concerns. Split:

- `tests/router/evals/` — router classification accuracy. Dataset: labeled `(message, expected_segments)` tuples. Metrics: single-intent accuracy, mixed-intent decomposition F1, false-positive rate on greetings-with-intent.
- `tests/agents/order/evals/` — order planner intent accuracy + response quality. Current `test_regression.py` and `test_capability.py` content moves here.
- `tests/agents/support/evals/` — support intent accuracy + response quality (once Phase 2 lands).
- `tests/agents/booking/evals/` — booking (future).
- `tests/e2e/evals/` — end-to-end scenario evals measuring full-stack behavior.

### Shared test harness

Keep a single top-level harness (`tests/_harness.py` or similar) with common fixtures:
- `business_context` fixtures per business type.
- `mock_llm` factory that returns canned responses.
- `session_factory` that builds isolated test session state.
- `conversation_history_factory` for multi-turn scenarios.

Each agent/router folder imports from this harness. Avoids duplicating fixtures.

### Migration plan

1. Create new folder structure, empty.
2. Move existing files one by one, keeping imports working via a compatibility layer if needed.
3. Update `pytest.ini` / `pyproject.toml` with the new paths.
4. Run full suite to confirm green.
5. Delete old folders.

Do this in its own PR, separate from Phase 0–3 changes. Mixing reorganization with functional changes makes reviews painful.

### What tests to write first for each new component

**Router tests** (when Phase 1 lands):
- Pure greeting → `greeting` (no agent dispatch).
- Greeting + product → `order` with cart intent.
- Menu query → `catalog` (no agent dispatch).
- Product name → `order` (ADD_TO_CART).
- Status question → `support`.
- Mixed intent → 2 segments.
- Ambiguous → falls back to primary agent.

**Support agent tests** (when Phase 2 lands):
- Order status with existing order → correct status returned.
- Order status with no order → helpful fallback.
- Business info queries → correct field from settings.
- Complaint → persists + user gets acknowledgement.
- Complaint with low confidence → escalates to human.

**Dispatcher tests** (when Phase 3 lands):
- Single segment → single agent, composer skipped.
- Two segments → both agents run in order, composer stitches.
- Handoff → second agent receives context, runs, result composed with first.
- Abort between hops → second agent not invoked, no partial send.
- Handoff cycle (A→B→A) → detected, aborted with warning.
- MAX_HOPS exceeded → best-effort output, warning logged.

### Existing tests to keep unchanged

- `tests/infrastructure/debounce/` (move from `tests/unit/test_turn_lock.py`, etc.) — tests debounce + abort + turn lock. Agent-agnostic. Keep behavior identical.
- `tests/services/catalog/test_product_search_retrieval.py` (move from `tests/unit/`) — catalog correctness. Already well-scoped.

### CI gating

Each component folder gets its own CI job matrix entry. PR touching:
- `app/agents/order/**` → run `tests/agents/order/**` + `tests/e2e/**`.
- `app/agents/support/**` → run `tests/agents/support/**` + `tests/e2e/**`.
- `app/orchestration/router.py` → run `tests/router/**` + `tests/e2e/**`.
- `app/services/catalog/**` → run `tests/services/catalog/**` + any agent test that uses catalog.
- **Planner prompts / response prompts** (any file with `PLANNER_SYSTEM_TEMPLATE` or `_build_response_prompt`) → run ALL evals (harness invariant from earlier).

This scoping makes most PRs fast while ensuring prompt changes get full regression coverage.

## Metrics to watch post-launch

- **Router latency**: p50, p95. Target: p95 < 500ms.
- **Router accuracy**: manual eval on 100 random turns per week.
- **Mixed-intent rate**: % of turns the router split. Expected: 5-10%.
- **Composer skip rate**: should match 1 - mixed-intent rate.
- **Support agent resolution rate**: % of support turns that produced a useful answer vs fallback.
- **Cross-domain session continuity**: users who successfully return to ordering after a support detour.

## References

- Session state slots convention: common in LangGraph (`TypedDict` state), CrewAI (shared context), and most production agent platforms.
- Router pattern: standard in Alexa skills, Google Assistant actions, Kore.ai.
- Anti-patterns intentionally avoided: sticky routing based on last agent; agents writing to each other's state; single god-state with no namespacing.

---

## Harness assessment (from "Agent = Model + Harness" framing)

Framing borrowed from Divy Yadav's "7 Agent Harness Components" article. Scoring our current bot against production-agent harness best practices:

| # | Component | Status | Notes |
|---|-----------|--------|-------|
| 1 | Control loop with MAX_STEPS | Partial | Order agent is fixed 3-step (planner → executor → response). Booking/sales agents have `max_iterations` loop. Add MAX_STEPS to any future agent that iterates on tools. |
| 2 | State management | Strong | Namespaced session slots per agent, Postgres-backed, `turn_cache` memoization. |
| 3 | Memory | Short-term only | Last 10 messages of conversation history. No long-term customer memory. **Gap — see Phase 5.** |
| 4 | Tools | Good | Tight, domain-specific, decent descriptions. Strict separation (no shared tools) is intentional. Bash escape hatch N/A for narrow-domain bot. |
| 5 | Context management | Partial | History cap (10 msgs × 200 chars) drops old messages without summarizing. **Gap — see Phase 6.** |
| 6 | Planning | Missing for conversational flow | No persistent plan file. `order_state` enum is an implicit mini-plan. OK for now; revisit if post-venta workflows span multiple sessions. |
| 7 | Error handling | Mostly fallbacks | User-visible Spanish fallbacks on every tool. No retry-with-backoff, no confidence thresholds, no explicit human-escalation path. **Gap — see Phase 7.** |

### Deliberately not applied

- **Bash escape hatch / sandbox execution** — irrelevant for narrow-domain chatbot. No code execution, no need for isolated runtime.
- **Ralph Loop (multi-context long-horizon autonomy)** — no task spans multiple context windows. Turns are short, state persists in DB.
- **Minute-long session context compaction** — our sessions are short-per-turn; cross-session compaction is handled by DB persistence + summarizer (Phase 6), not mid-turn token budgets.

---

## Phase 5: Long-term customer memory (post-support-agent)

**Scope**: Persistent per-customer facts that survive across sessions. First useful in the support agent ("tu último pedido fue X"), later reusable by order agent ("¿el de siempre?").

**Schema**:

```sql
CREATE TABLE customer_memory (
  id UUID PRIMARY KEY,
  customer_id INT REFERENCES customers(id) ON DELETE CASCADE,
  business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
  key TEXT NOT NULL,         -- e.g. "dietary_restriction", "usual_address", "payment_preference"
  value TEXT NOT NULL,       -- free-text fact
  confidence FLOAT DEFAULT 1.0,
  source TEXT,               -- "explicit" / "inferred" / "admin"
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(customer_id, business_id, key)
);
```

**Flow**:
1. End-of-turn (or end-of-session): lightweight LLM summarizer reads the turn, extracts new facts, upserts to `customer_memory`.
2. Start-of-turn: relevant facts (top-K by recency or key match) are loaded into the router/agent context as a "Lo que sabemos del cliente:" block.

**First useful keys**:
- `dietary_restriction` — "no come cebolla"
- `usual_address` — "carrera 7 #45-23"
- `payment_preference` — "nequi"
- `last_order_summary` — "el viernes pidió una barracuda"

**Rules**:
- Writes are additive. No automatic deletion.
- Admin console surfaces memory so humans can correct wrong inferences.
- Memory is **read** by any agent, **written** only by a shared memory-updater invoked end-of-turn by the dispatcher — NOT by individual agents (avoids write contention + keeps memory a shared capability, consistent with the strict separation rule: shared read-only fact store, not a shared tool that mutates behavior).

---

## Phase 6: Conversation history summarization

**Scope**: When history exceeds a threshold, summarize older messages instead of truncating. Prevents context rot in long post-venta conversations ("dónde está mi pedido" → "ya no quiero esperar" → "quiero cancelar" → "no, mejor espero").

**Design**:
- If `conversation_history.length > 15`, compress everything older than the last 6 messages into a single summary string.
- Summary is cached on the conversation row (not re-computed every turn).
- Planner prompt format becomes:

  ```
  Resumen de conversación anterior:
  <summary of msgs 1..N-6>

  Mensajes recientes:
  <last 6 msgs verbatim>

  Usuario: <current message>
  ```

- Task definition (system prompt) and last 6 messages **always stay verbatim**. Only the middle gets compressed.

**When to implement**: after support agent lands, once conversations start exceeding 15 turns. Measure before building.

---

## Phase 7: Confidence-based human escalation

**Scope**: Agents (especially support) can explicitly decide "this needs a human" rather than guessing. Integrates with the complaint logging infrastructure planned for Phase 2.

**Design**:
- Every agent's response generator can return an optional `escalate: true` flag alongside the reply.
- When escalation fires:
  - Flag the conversation in DB (`conversations.needs_human_review = TRUE`).
  - Disable the agent for this conversation (`conversation_agent_enabled = FALSE`) until admin re-enables.
  - Notify admin console (and optionally Slack/email).
  - Reply to user: "Un humano te va a contactar pronto."

**Triggers**:
- Explicit complaint intent in support agent.
- Multiple retries of the same failed tool call.
- Planner low-confidence fallback (planner returned `CHAT` after 2+ ambiguous turns).
- Any uncaught exception in the agent pipeline.

**Why this matters**: the article's clearest principle — "an agent that knows when to stop and ask for help is more useful than one that always tries to finish." Particularly critical for post-venta where mistakes are costly (wrongly refunded, wrongly cancelled).

---

## Harness invariant (applies to every phase)

> **Model ↔ harness coupling**: When we change tool descriptions, add intents, or restructure prompts, regression-test against `tests/evals/` every time. Small-looking prompt changes can measurably degrade performance on edge cases we've already validated.

This is not a bureaucratic checkbox. The article's point about Claude Code being post-trained with its harness applies proportionally to our planner prompt — it has been empirically tuned against Biela's traffic patterns and every rule has historical evidence behind it. Changes without eval runs are changes made blind.

**Enforcement**: CI should run the eval suite on PRs that touch `PLANNER_SYSTEM_TEMPLATE`, any agent's response-generator prompt, or any tool description. Block merge on regression.

---

## Migration-proofing: building LangGraph-ready without adopting LangGraph

### The concern

As we add bookings and more agents, mid-turn handoffs (agent A finishes, agent B continues, same turn) become necessary. The fear: we'll eventually be forced to migrate to LangGraph (or similar), and the migration will be painful.

### The analysis

Migration pain scales with **how coupled the code is to the current orchestration shape**, not with the framework swap itself. If agents, session state, and tools are clean abstractions with narrow contracts, moving to LangGraph is mostly rewiring the orchestrator — the agents barely change. Painful migrations happen when agents have implicit assumptions, shared mutable state, or tight coupling to the current orchestrator.

So the question isn't "when do we migrate?" — it's **"are we building agents that could survive a migration if one becomes necessary?"**

Migrating preemptively costs real money now (learning curve, unstable LangGraph API still pre-1.0, debugging complexity, onboarding friction) to avoid a speculative future cost. The cheaper move is to design cleanly and defer the decision.

### Four migration-proofing rules (treat as harness invariants)

**Rule 1 — Dispatcher is a thin orchestrator interface.**
The dispatcher's only job: "for each `(agent, segment)` in order, run the agent, collect reply, process handoffs." Narrow interface means swapping it for a `StateGraph.compile()` later is ~200 lines rewritten, not a rewrite of agents.

**Rule 2 — Agents are pure functions of their inputs.**
```
agent.execute(message, session_slot, business_context, history, ...) → {reply, state_update, handoff?}
```
No reaching into global singletons. No reading other agents' slots except through read-only accessors. No shared mutable state. This is already LangGraph's node contract — a node is `(state) → state_update`. If our agents match this shape, they port directly.

**Rule 3 — Session state is a single namespaced dict, not scattered fields.**
Already the case (`order_context`, `booking_context`, future `support_context`, `customer_memory`). Every slot addressable by a single key path. LangGraph's `TypedDict` state is the same pattern with type hints. Migration = add type hints + rename `save_session` to `state_reducer`.

**Rule 4 — Handoffs are explicit data, not implicit control flow.**
When the first handoff scenario lands, build it with an explicit payload: the first agent's response includes `handoff: { to: "order", context: {...} }`. The dispatcher sees this and runs the second agent. This is LangGraph's `Command(goto=...)` primitive. Our dispatcher implements it with an if-statement today; migration swaps it for the LangGraph primitive.

Code under these rules is mechanically portable to LangGraph. No design decisions deferred, no hacks to unwind.

### Explicit handoff contract (add to Phase 3 scope)

Every agent's `execute()` return shape becomes:

```python
{
    "agent_type": str,
    "message": str,
    "state_update": dict,          # agent's own namespaced slot
    "handoff": Optional[{          # NEW
        "to": str,                 # target agent_type
        "segment": str,            # message to pass forward
        "context": dict,           # structured handoff payload
    }],
}
```

Dispatcher logic becomes:

```
segments = router.decompose(user_message)
results = []

for (agent_type, segment) in segments:
    output = agent.execute(segment, ...)
    results.append(output)

    # Mid-turn handoff: agent requested another agent continue
    hop = 0
    while output.get("handoff") and hop < MAX_HOPS:
        hand = output["handoff"]
        output = agent_registry[hand["to"]].execute(
            message=hand["segment"],
            handoff_context=hand["context"],
            ...
        )
        results.append(output)
        hop += 1

final_reply = composer(results) if len(results) > 1 else results[0]["message"]
```

**Constraints**:
- `MAX_HOPS = 3` — caps runaway chains. Bumps to a warning log, returns best-effort output.
- Handoffs are acyclic. If A hands to B hands to A, reject on the second A invocation in the same turn (detect via `handoff_chain` stack).
- Handoff context is the ONLY way data flows between agents within a turn. No side-channel reads of another agent's `state_update`.

### The Biela case this unlocks

Concrete handoff scenarios from the earlier analysis:

1. **Bookings + pre-order combined** (future feature): booking agent creates reservation → returns `handoff: {to: "order", context: {booking_id: X}}` → order agent attaches pre-order to booking.
2. **Modify existing order** (future post-venta feature): support agent validates order is still modifiable → returns `handoff: {to: "order", context: {order_id: X, action: "add_item"}}` → order agent applies the mutation.

Neither exists today. But building the handoff primitive now costs ~1 day and **completely eliminates the "painful migration" risk** because the pattern maps 1:1 onto LangGraph's `Command(goto=...)` when/if we migrate.

### LangGraph migration triggers (defer until one of these hits)

Migrate to LangGraph (or another durable-execution framework) only when one of these concrete signals appears — not preemptively:

1. **Cyclic handoffs within a turn**: Agent A → B → A → C → A in the same turn. Our linear handoff dispatcher becomes a graph interpreter, poorly. LangGraph is the graph interpreter, well.
2. **Checkpointing + mid-turn resumability**: user sends a request, backend hits a third-party API taking >30s, connection drops — agent needs to resume from mid-execution. Building this manually is genuinely hard; LangGraph's checkpointer handles it natively.
3. **Streaming partial outputs mid-turn**: "Buscando tu reserva... Listo, encontré #X. Ahora agregando el pedido..." — progressive reveal during a multi-step handoff chain.
4. **Human-in-the-loop pauses mid-turn**: agent pauses, notifies admin on Slack, resumes when admin approves. Durable workflow semantics.

None of these apply to Biela's roadmap as discussed. Revisit when one concretely blocks us, not before.

### When the migration does happen

If one of the triggers fires, the port becomes:
- Dispatcher loop → `StateGraph.compile()` with nodes = agents, edges = handoff conditions.
- Session dict → `TypedDict` (just add type hints).
- `handoff` return field → `Command(goto=target, update=context)`.
- Response composer → final node in the graph.

Estimated effort under clean design: **1–2 week refactor, not a rewrite.** That's the version of "future-proofing" that actually works — design cleanly, defer the framework decision, migrate mechanically when forced.

---

## Abort pattern in multi-agent turns

### Current state

The mid-processing abort pattern (implemented in `project_debounce_abort.md`) is **already agent-agnostic**:

- **Set**: `debounce.py` sets `abort:{to_number}:{phone}` when a new message arrives while `processing:{to_number}:{phone}` flag exists.
- **Checked**: `whatsapp_handler._run_agent_and_send()` checks after the agent returns, before `send_message()`.

Because the check lives in the handler (not in the order agent), it works for any agent. Support, booking, marketing will get abort behavior for free — no changes needed there.

### What needs to change for multi-agent

**Extension 1: Abort check between handoff hops (add in Phase 3).**

With mid-turn handoffs, a turn can chain multiple agents:

```
turn starts → agent A runs → handoff → agent B runs → composer → send
```

If a new user message arrives between A and B, running B is wasted work. Add one `check_abort()` in the dispatcher between hops:

```python
for (agent_type, segment) in segments:
    if check_abort(abort_key):
        return
    output = agent.execute(...)
    while output.get("handoff") and hop < MAX_HOPS:
        if check_abort(abort_key):
            return
        output = next_agent.execute(...)
```

Cost: ~1ms per check (Redis `EXISTS`). Savings: potentially seconds of wasted LLM compute on multi-agent turns.

**Extension 2: Per-agent mid-pipeline abort (optional, deferred).**

Each agent's pipeline has multiple stages (e.g. order agent: planner → executor → response generator). An abort mid-pipeline could save later stages. BUT state consistency constraints apply:

| Abort point | Safe? | Why |
|-------------|-------|-----|
| Before planner runs | ✅ Safe | Nothing mutated yet |
| After planner, before executor | ✅ Safe | Planner output is informational only |
| After executor, before response generator | ❌ **UNSAFE** | State is mutated (cart changed, order placed). Skipping the response means user never sees confirmation of a change that happened. Next message references state the user didn't know about. |
| At send time (current) | ✅ Safe | Last line of defense; state is consistent but user hasn't seen the reply |

**Invariant**: do NOT abort between executor and response generator. If the executor mutated state, the user must see the confirmation. Partial state with no confirmation is worse than a slightly stale reply.

If we add mid-pipeline abort at all, the only valid place is **between planner and executor** — after the planner returns, before any mutation. This saves ~1.3s on aborted turns (executor + response LLM).

**When to build this**: defer until metrics justify it. If abort fires on <5% of turns, extension 2 is micro-optimization. If impatient users cause abort to fire on >10% of turns, it becomes worth the per-agent complexity. Measure in LangSmith / structured logs before building.

### Summary of abort changes across phases

| Change | Where | When | Savings |
|--------|-------|------|---------|
| Already implemented | Handler pre-send check | — | Prevents stale message reaching user |
| Already implemented (all agents) | Handler-level, agent-agnostic | — | Support/booking/marketing inherit it |
| Check between handoff hops | Dispatcher | **Phase 3** | Saves N-1 agent runs on aborts during multi-agent turns |
| Check between planner and executor (per agent) | Each agent | Deferred — measure first | Saves executor + response LLM on single-agent aborts |
| **Never abort** between executor and response generator | Invariant | — | State consistency |
