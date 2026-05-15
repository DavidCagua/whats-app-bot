# Changelog

Notable changes to the WhatsApp bot backend. Date-based (no tagged releases).
Each entry: what changed + commit SHA + link to the incident or PR that has the
full story. Keep entries short — if you're writing a paragraph, it belongs in
the commit message.

Roadmap lives at the bottom. Roadmap entries are one-liners pointing to a
file; when you pick one up, open an issue or a design doc for the real
context.

---

## 2026-04-25 (debounce coalescing + abort carry-forward)

- `85b8641` fix(debounce): coalesce burst messages and carry-forward on
  abort. Bursts within `DEBOUNCE_SECONDS` (now env-driven, default
  `1.5`) collapse into one planner call; aborted turns RPUSH their text
  back into the same Redis list so the next flusher merges aborted +
  newer messages. Fixes the Biela `+573150490281` / `+573177000722`
  case where "una picada" + "que valor?" produced a generic "¿en qué
  puedo ayudar?" reply because only the last message reached the
  planner. Scales to N consecutive aborts. State machine untouched —
  abort still fires before executor, so no rollback needed. 16 new
  unit tests. Set `DEBOUNCE_SECONDS=1.5` on Railway.

---

## 2026-04-16 (disambiguation overhaul + perf caching)

Big day. Two debug sessions on Biela wa_ids `+573242261188` and
`+573177000722` uncovered a cascade of disambiguation failures and
silent drops. Fixed in 4 commits + one perf bundle.

### Disambiguation + search

- `1f5f282` fix(order): generic-product match + multi-item fault
  tolerance + name verbatim rule. Three-layer fix for the
  `+573242261188` incident ("jugo de mora en leche y soda de frutos
  rojos" → disambiguation loop + soda dropped). Fix A: multi-item
  executor loop no longer re-raises `AmbiguousProductError` on first
  item — captures it, continues, and surfaces partial success +
  pending clarification in one response. Fix B: new token-containment
  decisive rule in `search_products` for generic products with
  qualifier (e.g. "jugo de mora en leche" → `Jugos en leche` +
  `_derived_notes="mora"`). Fix C: response generator prompt now
  forbids fabricating product names. 7 new unit + 1 eval.

- `66de4e5` fix(search): token-set equality decisive rule (Bug 5).
  "Una soda de frutos rojos" → `Soda Frutos rojos` wins decisively
  even when Coca-Cola is a strong embedding neighbor. New rule 1c:
  when stemmed content-token set of query == exactly one candidate,
  promote it. Gated on ≥2 tokens so Corona/Michelada single-token
  prefix-rival disambiguation stays intact. 5 unit + 1 eval.

- `7807b9e` fix(order): planner/executor cleanups for 4 regressions
  (Bugs 1/2/6/7) from the `+573177000722` session. Bug 1: flavor
  lost on disambiguation reply — new deterministic
  `apply_disamb_reply_flavor_fallback` post-processor extracts
  qualifier tokens the planner dropped and injects them as `notes`.
  Bug 2: `ProductNotFoundError` (new exception) replaces the old
  error-string return from `add_to_cart` so multi-item batch can
  surface "not found" items in the response. Bug 6: `VIEW_CART`
  keyword rules in planner prompt. Bug 7: notes-addition
  `UPDATE_CART_ITEM` rule ("el jugo también es de mora"). +3 unit,
  +8 integration, +2 eval.

- `9dd8a2c` feat(search): LLM disambiguation resolver (Bug 4).
  When deterministic rules can't pick a winner, a ~$0.0001
  gpt-4o-mini call resolves the ambiguity. Three outcomes: WINNER
  (with optional derived notes), FILTERED (exclude wrong-category
  candidates — fixes Hervido Mora leaking into "jugo de mora"
  queries), or AMBIGUOUS (all candidates shown). Deterministic rules
  still fire first as fast paths; LLM is the catch-all for the long
  tail. Falls back to full disambiguation on API failure. 4 unit +
  1 eval.

- `7d05c9e` fix(search): prevent LLM resolver from auto-picking
  sub-type defaults. "Un jugo de mora" was resolved as WINNER →
  Jugos en agua instead of FILTERED → [agua, leche] with a prompt.
  Added CRITICAL rule: never auto-pick when candidates differ only
  by a sub-type the customer didn't specify.

### Performance (webhook + order flow)

- `4b8395e` perf(dedupe): atomic Redis claim on the webhook hot path.
  Replaces the old two-round-trip Supabase dedupe (SELECT + INSERT)
  with a single Redis `SET NX EX`. Drops pre-debounce webhook
  latency from ~2s to ~50ms.

- `a358fe2` perf(routing): indexed whatsapp_numbers lookup + TTL
  cache + JOIN. Migration 024 canonicalizes `phone_number` to
  `+<digits>` and adds a partial unique index. Service layer does one
  query with `joinedload(Business)` instead of two sequential
  sessions. Module-level 5-min TTL cache eliminates the round trip
  for warm requests. Invalidated on create/update writes.

- `a118232` perf(debounce): buffer before business lookup, resolve in
  flusher. Moves the Supabase business-context lookup out of the
  webhook thread and into the debounce flusher, so the 3s quiet
  window starts on message arrival, not after a ~3–5s DB round trip.

- `2b7ded9` perf(order-flow): Tier 1 per-turn memoization cache.
  `app/orchestration/turn_cache.py` — `contextvars`-backed TurnCache.
  Collapses 2–4× per-turn reads of session state, customer, and
  product search into O(1). Explicit invalidation after every
  `session_state_service.save`. Pre-populates customer from the
  handler gate. 11 unit tests.

- `94c9f97` perf(order-flow): Tier 2 catalog cache + business_id
  context cache. `app/services/catalog_cache.py` — 5-min TTL process-
  memory cache for `list_categories`, `list_products`,
  `list_products_with_fallback`. Also extends `business_service`
  cache to `business_id` keys. Staleness contract: admin writes live
  in the Next.js admin console (Prisma), so TTL is the only
  invalidation mechanism for now. 11 unit tests for both cache tiers.

### Debounce

- `97f1066` feat(webhook): debounce rapid messages per phone via
  Redis. 3s quiet window keyed by `(to_number, phone)`. Lua scripts
  for atomic RPUSH + SET NX (buffer) and LRANGE + DEL (drain).
  Flusher thread calls `process_whatsapp_message` + `turn_lock` after
  the window. Falls back to sync on Redis unavailable.

- `e6a5e42` → `3a8624c` → `b16c52b` → `c573046` iterative fixes:
  cross-business key scoping, atomic Lua buffer, root-logger for
  Railway visibility, debug logging for NX return value.

### Test coverage

98 unit / 15 integration / 13 eval (+1 xfail). Up from 68/7/9 at
the start of the day.

## 2026-04-14 (turn lock)

- `eb8dd64` fix(views): per-wa_id turn serialization via Postgres advisory
  lock. Closes the double-reply race where two consecutive messages from
  the same user ran in parallel and the second loaded stale session
  state. New `app/services/turn_lock.py` + 12 unit tests. Closes
  Roadmap item #2.

## 2026-04-13 (carta + denver)

- `8ca5523` fix(search,menu): denver disambiguation + menu link in carta
  replies. Fourth exact-match rule in `_score_product` for Spanish
  `"[category] [name]"` phrasings (Biela "un perro caliente denver" no
  longer disambiguates). MENU_CATEGORIES response branch now includes
  `business.settings.menu_url` and adapts shape based on whether the
  user explicitly asked for the link. Two new eval scenarios pin
  both. Convention: trajectory match only for stable routings.

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
   `QUESTION`, `CANCEL`). See [app/orchestration/order_flow.py](app/orchestration/order_flow.py).
2. ~~Offline transcript replay test harness.~~ **Done.** `tests/evals/` with
   `agentevals` trajectory pattern (commit `a2b1c4f`). 13 regression evals.
3. ~~Per-`wa_id` turn serialization.~~ **Done.** Postgres advisory lock
   (commit `eb8dd64`) + Redis debounce (commit `97f1066`).
4. **Multi-tenant planner prompts.** Move the hard-coded Biela phrasing out of
   `PLANNER_SYSTEM_TEMPLATE` in [app/agents/order_agent.py](app/agents/order_agent.py) into per-business
   `settings.planner_prompt_extras`. Blocked on a second tenant.
5. **Redis-backed catalog cache invalidation.** Currently the catalog cache
   (`app/services/catalog_cache.py`) has a 5-min TTL as the only invalidation
   mechanism because product writes live in the Next.js admin console (Prisma).
   If admins complain about lag, implement a Redis version-stamp: admin console
   bumps `catalog:version:{business_id}` on write, Python cache checks version
   on read (~1ms). Design sketch in the `catalog_cache.py` module docstring.
6. **Sub-type correction planner rule.** When the user corrects a sub-type
   after auto-add (e.g. "en agua no en leche"), the planner currently routes
   to `UPDATE_CART_ITEM` with `notes="sin leche"` (ingredient exclusion) instead
   of recognizing it as a sub-type confirmation. Low priority since the LLM
   resolver prompt fix (`7d05c9e`) prevents the auto-add that triggers this, but
   the correction path should be robust independently.
7. **LLM resolver eval stability.** The resolver prompt works well on
   gpt-4o-mini at temp=0 but the "jugo de mora" sub-type case needed a
   follow-up prompt fix (`7d05c9e`). Monitor for more edge cases; consider
   adding few-shot examples directly in the prompt if other sub-type patterns
   surface. Long-term: structured output (JSON mode) would remove the
   regex-parsing fallback.
