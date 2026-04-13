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

- Two production bug fixes from the Biela 2026-04-13 transcript +
  regression evals for both:
  - **fix(search): exact-match for "[category] [name]" phrasings.**
    `"un perro caliente denver"` was being disambiguated against all
    four hot-dog options because `DENVER` is a single-word product name
    and every hot dog shares the `perro caliente` tag — so the scorer
    couldn't reach the 2x ratio threshold to declare a winner. Added a
    fourth exact-match rule in `_score_product`: when a single-word
    product name appears as a query token AND every other query token
    matches that product's category or tags, treat it as an exact
    match. Doesn't fire on incidental mentions because of the tag/cat
    gate.
  - **fix(menu): include menu URL in MENU_CATEGORIES responses.** When
    user asks for the menu/carta, the bot was narrating category names
    as text and silently dropping `business.settings.menu_url`. The
    `MENU_CATEGORIES` response branch now extracts the URL from the
    business context and the prompt instructs the LLM to: lead with the
    URL when the user explicitly asked to be sent the menu/carta/link;
    otherwise lead with categories and offer the URL at the end. Single
    adaptive response, no new intent.
  - Two new eval scenarios in `tests/evals/test_regression.py`:
    `test_perro_caliente_denver_no_disambiguation` and
    `test_menu_link_sent_when_user_asks_for_carta`.
  - Eval harness improvements: `_FakeProductService.get_product` now
    routes through the scenario's `stub_search_products`, patched
    `app.services.order_tools.{product_order_service,session_state_service}`
    so `@tool` calls go through the fakes, and added
    `tool_args_match_mode` to `AgentScenario` for scenarios where the
    planner has multiple valid arg shapes for the same intent.
  - Dropped reference trajectories from the pizza/sushi scenarios
    because the planner routes `"hay pizza?"` inconsistently between
    `LIST_PRODUCTS` and `SEARCH_PRODUCTS` across runs — both produce
    identical behavior, response-text + LLM judge cover them. New
    convention: trajectory match only for stable routings.
  - Commit `<pending>`.

## 2026-04-13 (evals)

- `a2b1c4f` test(evals): adopt LangChain agentevals trajectory pattern.
  `tests/evals/_harness.py` synthesizes a LangChain-shaped trajectory
  from each hermetic pipeline run and asserts via
  `create_trajectory_match_evaluator` (deterministic, superset mode) +
  `create_trajectory_llm_as_judge` (prose) + a response-text regex
  layer for guardrails trajectory match can't express. 6 regression
  scenarios + 1 capability xfail. New `eval` pytest marker, gated on
  `OPENAI_API_KEY`.

## 2026-04-13 (later)

- `ac2a6a3` fix(search): stop embedding lane from hallucinating matches.
  Cosine floor + pure-embedding filter extended to SEARCH_PRODUCTS +
  category existence pre-check + `matched_by` signal + 13 regression
  tests. Fixes Biela "pizza → burgers" / "sushi → burgers" / "perro
  caliente denver → Denver + noise" false positives.

## 2026-04-13

- `ae05da7` feat(order): semantic CONFIRM intent + rejection recovery —
  executor resolves CONFIRM by state; allowlist rejections become
  [INVARIANT] logs + soft recovery; new [ORDER_TURN] structured log.
  Fixes Biela order abandonment, wa_id `+573147624802`.

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
