---
name: debug-conversation
description: Debug a WhatsApp bot conversation for a specific phone number — pulls the last conversation turns from Supabase, fetches matching Railway logs, and correlates user messages with executor/planner traces. Use when the user asks things like "why did the bot reply X to <number>", "check what happened with <number>", "debug this conversation", or pastes a WhatsApp transcript and asks for the server side.
---

# debug-conversation

End-to-end debugger for a single user's recent WhatsApp interaction. Given a phone number (or a transcript the user pastes), pull both sides of the story:
1. **What the DB saw** — conversation rows from Supabase.
2. **What the server did** — Railway logs around the same window.
3. **Correlate** — match user messages to planner intents, tool calls, and errors.

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

### 2. Pull matching Railway logs

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

### 3. Correlate and report

Build a compact timeline the user can scan. Format each turn like:

```
[20:06:24] user → "para que a la picada no le pongan morcilla"
[20:06:30] planner → UPDATE_CART_ITEM {product_name=PICADA, notes=sin morcilla}
[20:06:30] executor → cart re-opened (READY_TO_PLACE → ORDERING)
[20:06:30] assistant ← "Listo, hemos actualizado tu pedido: 1x PICADA (sin morcilla)..."
           timing: 3.5s total, send 0.5s
```

For each turn, note:
- **User text** (from DB)
- **Planner intent + params** (from logs)
- **Executor trace** — bypass/disamb/cart re-open/state transitions
- **Tool calls** — which tools ran, any errors
- **Bot reply** (from DB)
- **Timing** — total latency and any slow phases
- **Errors** — Twilio failures, exceptions, missing rows

End the report with a one-line diagnosis: what went wrong, or "flow looks healthy".

## Practical notes

- **Always use both wa_id formats** in the SQL `IN (...)` clause — `+<digits>` for Twilio, `<digits>` for Meta. The bot has both paths.
- **Prefer local Bogotá time** in reports (`AT TIME ZONE 'America/Bogota'`) so timestamps match what the user sees on WhatsApp.
- **Scope the log fetch** — Railway logs can be huge. Always pull a time slice, not the whole tail.
- **Never dump raw log blobs** into the final reply. Extract relevant lines and paraphrase. The user wants the story, not the stack trace.
- **Respect privacy** — redact name / address / exact phone when the user shares the session with someone else. The phone's last 4 digits are enough for reference.
- **If something is missing**, say so explicitly ("no server logs for this turn — possibly dropped before reaching the webhook") instead of guessing.

## Quick reference — key files the logs reference

- [app/agents/order_agent.py](app/agents/order_agent.py) — planner + response generator
- [app/orchestration/order_flow.py](app/orchestration/order_flow.py) — state machine, tool dispatch, disambiguation
- [app/services/order_tools.py](app/services/order_tools.py) — the `@tool`-decorated cart operations
- [app/services/product_search.py](app/services/product_search.py) — `search_products`, `AmbiguousProductError`
- [app/utils/whatsapp_utils.py](app/utils/whatsapp_utils.py) — send path, Twilio chunking
- [app/utils/twilio_utils.py](app/utils/twilio_utils.py) — inbound webhook parsing
- [app/views.py](app/views.py) — Flask routes
