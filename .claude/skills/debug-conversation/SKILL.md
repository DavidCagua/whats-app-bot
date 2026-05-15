---
name: debug-conversation
description: Debug a WhatsApp bot conversation for a specific phone number — pulls conversation turns from Supabase, structured planner/tool traces from LangSmith, and matching Railway logs, then correlates everything into a single timeline. Use when the user asks things like "why did the bot reply X to <number>", "check what happened with <number>", "debug this conversation", or pastes a WhatsApp transcript and asks for the server side.
---

# debug-conversation

End-to-end debugger for a single user's recent WhatsApp interaction. Given a phone number (or a transcript the user pastes), pull all three sides of the story:
1. **What the DB saw** — conversation rows from Supabase.
2. **What the LLM did** — structured planner/tool traces from LangSmith.
3. **What the server did** — Railway logs around the same window (everything outside the LangChain runtime).
4. **Correlate** — match user messages to planner intents, tool calls, and errors.

## When to invoke

Trigger automatically when the user:
- Names or pastes a phone number and asks "what happened" / "why did it say X" / "debug this"
- Shares a WhatsApp transcript and asks for the server side
- Reports a bug with a specific user and asks you to investigate

Do **not** invoke for generic bug reports with no user context — ask which number first.

## Inputs

- **Phone number (required)**: normalize to the `wa_id` format used in the DB.
  - Twilio path: `+573001234567` (with leading `+`)
  - Meta path: `573001234567` (digits only)
  - If unsure which one this bot uses, run the query twice — once with `+`, once without. The Twilio path is the current default.
- **Time window (optional)**: default to the last 2 hours. User can specify e.g. "today", "last 30 min", "around 8 PM".
- **Business ID (optional)**: only needed if this bot is multi-tenant and the user specifies a business. Otherwise skip the filter.

## Workflow

### 1. Pull conversation history from Supabase

Use the Supabase MCP tool. Project ID: `kdiiafmxhaqnfarsjsjg` (DavidCagua's Project, us-east-2).

Run this SQL (adjust the `wa_id`, `business_id`, and time window as needed):

```sql
SELECT
  c.timestamp AT TIME ZONE 'America/Bogota' AS ts_local,
  c.role,
  c.message_type,
  c.agent_type,
  LEFT(c.message, 300) AS message_preview,
  b.name AS business_name
FROM conversations c
LEFT JOIN businesses b ON b.id = c.business_id
WHERE c.whatsapp_id IN (:wa_id_plus, :wa_id_digits)
  AND c.timestamp > NOW() - INTERVAL '2 hours'
ORDER BY c.timestamp ASC
LIMIT 50;
```

Key facts about the `conversations` table (from [app/database/models.py](app/database/models.py#L354-L390)):
- `whatsapp_id` — the phone number (string, may or may not have leading `+`)
- `role` — `'user'` or `'assistant'`
- `message` — text content
- `message_type` — `'text' | 'audio' | 'image' | 'document'`
- `agent_type` — which sub-agent handled it (`order`, `booking`, etc.)
- `timestamp` — timezone-aware (America/Bogota for display)

If no rows come back, try:
- Widening the time window to 24 hours
- Dropping the `+` prefix (Meta path stores digits only)
- Checking `customers` table for the phone's canonical form: `SELECT phone, wa_id FROM customers WHERE phone LIKE '%<last 7 digits>%';`

### 2. Pull the LangSmith trace

Use the LangSmith MCP (`mcp__langsmith__*`). LangSmith auto-traces every LangChain call (planner LLM, tools, sub-chains) when `LANGCHAIN_TRACING_V2=true` is set in the bot's env, so this is the *structured* view of what the LLM did — much faster than grepping Railway.

The project name in LangSmith is **`whatsapp-bot`**.

**Workflow:**
1. `list_projects` — confirm `whatsapp-bot` exists and grab its ID.
2. `fetch_runs` — filter by project + the time window matching step 1's conversation timestamps. Pull root runs first for a top-level view, then drill into interesting ones.
3. For each interesting root run, fetch the full trace tree to see planner inputs, tool calls, and LLM outputs.

**Filtering by user:**
Ideally traces carry `wa_id` (or hashed wa_id) as run metadata. If they don't yet, fall back to:
- Filter by time window only, then match by trace count vs. message count from step 1.
- Search the run inputs for the user's message text — the planner receives the raw message in the prompt.

If this becomes painful, instrument with `RunnableConfig(metadata={"wa_id": ..., "business_id": ...})` at the agent entrypoint so traces become filterable by user.

**What to extract per turn:**
- Root run status (success / error) and total latency
- Planner LLM input (system prompt + recent history seen by the model)
- Planner output — the structured intent + params (e.g. `UPDATE_CART_ITEM {...}`)
- Tool calls — name, input, output, latency, error
- Token counts (cost signal; oversized prompts show up here first)

**Red flags:**
- Run status `error` — open the failed span first; the exception type usually tells the story
- A single LLM call > 5s — model congestion or an oversized prompt (check token count)
- Tool call errors the agent recovered from silently — still a bug worth flagging
- Planner picked an intent that doesn't match the user's apparent goal — copy the input prompt for review; it's often a system-prompt or context issue, not a model issue
- Missing trace for a turn that exists in Supabase — the LangChain runtime didn't run; check Railway for an upstream failure (webhook/auth/debounce)

### 3. Pull matching Railway logs

Use `mcp__Railway__get-logs`. If you don't already know the project/service, first call `mcp__Railway__list-projects` → `mcp__Railway__list-services` to find the whats-app-bot deployment service.

**Search strategy** (filter server-side when the MCP tool allows):
1. Grep for the **normalized phone number** — log lines include `wa_id=+573001234567` or `Extracted wa_id: +573001234567`.
2. Narrow by time window matching the conversation timestamps from step 1.
3. For each user message, look for these log markers in order:
   - `[DEBUG] ========== PROCESSING MESSAGE ==========`
   - `[MESSAGE] Processing message from <name> (<wa_id>): <text>`
   - `[CONVERSATION_MANAGER] Routed to agent=<order|booking>`
   - `[ORDER_AGENT] pending_disambiguation loaded=...`
   - `[ORDER_AGENT] Planner intent=<INTENT> params=<...>`
   - `[ORDER_FLOW] ...` (executor decisions — cart re-open, bypass resolution, state transitions)
   - `[ORDER_TOOL] <tool_name>` (tool invocations)
   - `[TIMING] send_message took <N>s` + the Twilio/Meta response

**Red flags to call out**:
- `❌ Failed to send message to WhatsApp API` → delivery failure (chunking bug, rate limit, invalid recipient)
- `Unable to create record: The concatenated message body exceeds the 1600 character limit` → Twilio 21617 (message too long — see [_split_for_twilio](app/utils/whatsapp_utils.py))
- `AmbiguousProductError` → disambiguation raised
- `pending_disambiguation loaded=True` → user is answering a disamb prompt
- `Re-opening cart` → cart-mutating intent arrived after checkout
- Any Python traceback
- Gaps longer than a minute between a user message arriving and the reply sending — investigate LLM latency or a silent failure

### 4. Correlate and report

Build a compact timeline the user can scan. Format each turn like:

```
[20:06:24] user → "para que a la picada no le pongan morcilla"
[20:06:30] planner → UPDATE_CART_ITEM {product_name=PICADA, notes=sin morcilla}
           (LangSmith run abc123, 2.1s, 1840 prompt / 84 completion tokens)
[20:06:30] executor → cart re-opened (READY_TO_PLACE → ORDERING)
[20:06:30] tool → update_cart_item(...) ✓
[20:06:30] assistant ← "Listo, hemos actualizado tu pedido: 1x PICADA (sin morcilla)..."
           timing: 3.5s total, send 0.5s
```

For each turn, note (and prefer LangSmith for the LLM-side fields, Railway for the rest):
- **User text** (Supabase)
- **Planner intent + params** (LangSmith — the structured planner output) — fall back to Railway `[ORDER_AGENT] Planner intent=...` if LangSmith trace is missing
- **Executor trace** — bypass/disamb/cart re-open/state transitions (Railway)
- **Tool calls** — which tools ran, any errors (LangSmith for inputs/outputs/latency, Railway for orchestration context)
- **Bot reply** (Supabase)
- **Timing** — LLM time from LangSmith, send time from Railway
- **Errors** — Twilio failures, exceptions, missing rows (Railway), planner exceptions / tool errors (LangSmith)

End the report with a one-line diagnosis: what went wrong, or "flow looks healthy".

## Practical notes

- **Always use both wa_id formats** in the SQL `IN (...)` clause — `+<digits>` for Twilio, `<digits>` for Meta. The bot has both paths.
- **Prefer local Bogotá time** in reports (`AT TIME ZONE 'America/Bogota'`) so timestamps match what the user sees on WhatsApp.
- **Use the right tool for each question** — LangSmith for "what did the LLM/planner do" (structured, fast), Railway for "what did the rest of the server do" (everything outside LangChain), Supabase for "what did the user actually see". Don't grep Railway for planner intents if LangSmith has the trace.
- **Scope the log fetch** — Railway logs can be huge. Always pull a time slice, not the whole tail. Same for `fetch_runs` — bound by time window.
- **Never dump raw log blobs or trace JSON** into the final reply. Extract relevant lines and paraphrase. The user wants the story.
- **Respect privacy** — redact name / address / exact phone when the user shares the session with someone else. The phone's last 4 digits are enough for reference.
- **If something is missing**, say so explicitly. "No LangSmith trace for this turn but Railway shows the request landed" is a real signal — usually means the LangChain runtime crashed or was bypassed.

## Quick reference — key files the logs reference

- [app/agents/order_agent.py](app/agents/order_agent.py) — planner + response generator
- [app/orchestration/order_flow.py](app/orchestration/order_flow.py) — state machine, tool dispatch, disambiguation
- [app/services/order_tools.py](app/services/order_tools.py) — the `@tool`-decorated cart operations
- [app/services/product_search.py](app/services/product_search.py) — `search_products`, `AmbiguousProductError`
- [app/utils/whatsapp_utils.py](app/utils/whatsapp_utils.py) — send path, Twilio chunking
- [app/utils/twilio_utils.py](app/utils/twilio_utils.py) — inbound webhook parsing
- [app/views.py](app/views.py) — Flask routes
