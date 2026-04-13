# Changelog

Notable changes to the WhatsApp bot backend. Date-based (no tagged releases).
Each entry: what changed + commit SHA + link to the incident or PR that has the
full story. Keep entries short — if you're writing a paragraph, it belongs in
the commit message.

Roadmap lives at the bottom. Roadmap entries are one-liners pointing to a
file; when you pick one up, open an issue or a design doc for the real
context.

---

## [Unreleased]

- Semantic `CONFIRM` intent + rejection recovery. Planner stops picking
  `PROCEED_TO_CHECKOUT` vs `PLACE_ORDER` for confirmation verbs; executor
  resolves `CONFIRM` by state. Allowlist rejections now emit `[INVARIANT]`
  logs + soft recovery instead of user-facing errors. New `[ORDER_TURN]`
  structured log per turn. Fixes the Biela abandonment bug (2026-04-13,
  wa_id `+573147624802`). Commit `<pending>`.

## 2026-04-13

- `0803861` fix(order): disambiguate prefix matches + atomic variant swap
- `a17e045` fix(twilio): chunk long messages to avoid 1600-char limit
- `68d104b` fix(order-flow): reuse sender wa_id as phone on "este número"
- `d98f445` fix(order-agent): show descriptions for all listed products
- `2647c01` fix(order-flow): re-open cart on mutation from COLLECTING_DELIVERY

## 2026-04-12

- `99cbc13` feat(orders): structured response pipeline + hybrid product search + per-item notes

## 2026-03-15

- `9f89a1b` Order flow: planner/executor, multi-item add, delivery details

---

# Roadmap

Deferred work, priority order. Open an issue when you pick something up.

1. **Full semantic-intent vocabulary restructure.** `CONFIRM` is the pilot;
   migrate the other 13 transitional intents (`ADD_TO_CART`, `PROCEED_TO_CHECKOUT`,
   `PLACE_ORDER`, …) to semantic ones (`ADD_ITEM`, `MODIFY_ITEM`, `PROVIDE_INFO`,
   `QUESTION`, `CANCEL`). Blocked on item 3. See [app/orchestration/order_flow.py](app/orchestration/order_flow.py).
2. **Offline transcript replay test harness.** Fixture-driven pipeline test
   (planner + executor) — required safety net before item 1. First fixture
   should be the Biela "Procedemos" session. See [tests/unit/test_order_flow_confirm.py](tests/unit/test_order_flow_confirm.py) for the current (isolated) test style.
3. **Per-`wa_id` turn serialization.** Postgres advisory lock in
   [app/views.py](app/views.py) `handle_twilio_message`. Fixes the double-reply race from the
   Biela session (two messages 5s apart, parallel planner passes, stale reply).
4. **Multi-tenant planner prompts.** Move the hard-coded Biela phrasing out of
   `PLANNER_SYSTEM_TEMPLATE` in [app/agents/order_agent.py](app/agents/order_agent.py) into per-business
   `settings.planner_prompt_extras`. Blocked on a second tenant.
