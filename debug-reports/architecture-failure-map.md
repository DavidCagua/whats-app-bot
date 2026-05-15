# Architecture failure map — Tue→Thu 2026-05-05 / 06 / 07

For every conversation in the three day-reports that exhibited a bug, this document pinpoints **which system component failed**, **which commit patched it**, and **why that patch worked (or didn't)**.

The order agent gets its own deep section at the end — the user is planning to redesign it, and most of the week's pain lived there.

---

## System component vocabulary

Using the actual file boundaries from this codebase:

| Layer | Files | Responsibility |
|---|---|---|
| **Inbound** | `app/handlers/whatsapp_handler.py` | Twilio webhook → `process_whatsapp_message` |
| **Debouncer / turn lock** | `app/services/debounce.py`, `app/services/turn_lock.py` | Coalesce rapid messages, abort in-flight turns, serialize per-`wa_id` |
| **Router** | `app/orchestration/router.py` | Greeting fast-path, domain classification (order / CS / booking / greeting), order-availability gate |
| **Dispatcher** | `app/orchestration/dispatcher.py` | Hand turn to the right agent flow |
| **Conversation manager** | `app/orchestration/conversation_manager.py`, `app/database/session_state_service.py` | Per-`wa_id` session state, history window, turn cache |
| **Order agent — Planner** | `app/agents/order_agent.py` | LLM that emits structured intents from a fixed enum |
| **Order agent — Executor** | `app/orchestration/order_flow.py` | State machine + tool dispatch, owns `order_context` |
| **Order agent — Tools** | `app/services/order_tools.py` | `add_to_cart`, `place_order`, `cancel_order`, etc. — supposed to own DB writes |
| **Order agent — Response generator** | `app/orchestration/response_composer.py` | Second LLM that turns the executor's typed result into customer Spanish |
| **CS agent** | `app/agents/customer_service_agent.py`, `app/orchestration/customer_service_flow.py` | Hours, menu link, payment details, complaints |
| **Catalog / Search** | `app/services/catalog_service.py`, `product_search.py`, `embeddings.py`, `tag_generator.py`, `product_metadata.py` | Resolve product names → catalog IDs |
| **Cancel keywords** | `app/services/cancel_keywords.py` | Substring-match list for hard cancel detection |
| **Admin panel** | (frontend repo) + admin API | Operator tools: cancel, edit, "Crear pedido" |

---

## Per-conversation pinpoint

Conversations from the three reports, ordered by Bogotá time. Only conversations with an actual bug appear here. **Component** uses the vocabulary above. **Why** explains the underlying defect, not just the symptom.

### Tue 2026-05-05

**T-1 · +573147139789 · "porfsvor" typo wiped context**
- *Component:* Conversation manager — history window too small.
- *Patch:* `636dc10` uniform 10-msg history across router + planners.
- *Why:* Router and planners now read the same 10-msg window, so a single noisy message can't kick the planner back to "first turn" mode.

**T-1 · +573147139789 · "hay atencion" not routed to hours**
- *Component:* CS agent — anchor list.
- *Patch:* `66329dc` add 'hay servicio' to hours-field anchors.
- *Why:* Anchor-substring matching grew the list. Brittle long-term.

**T-3 · +573147554464 · "Tienes la X?" → ADD_TO_CART**
- *Component:* Order agent — Planner misclassified interrogative as imperative.
- *Patch:* `7ec4aa2` route interrogative pronominal references to GET_PRODUCT.
- *Why:* Planner instructions now have a rule: yes/no question forms about products → GET_PRODUCT, never ADD_TO_CART.

**T-3 · +573147554464 · Operator placed order off-system, no DB row**
- *Component:* Admin panel — missing "Crear pedido" path.
- *Patch:* `483b36b` (Thu) admin-console "Crear pedido" button.
- *Why:* Operators got an in-product path that writes to `orders`, removing the temptation to copy-paste bot templates.

**T-3 · +573147554464 · Operator turns indistinguishable from bot**
- *Component:* Conversation manager — schema missing `agent_type`.
- *Patch:* `7771e35` (Thu) tag operator turns.
- *Why:* Started populating `agent_type` so future analyses stop having to use the casing heuristic.

**T-4 · +573159280840 (dev) · LA VUELTA & MANHATTAN added as phantom products**
- *Component:* Catalog / Search + Order agent — Tools. `add_to_cart` accepted unknown names instead of refusing.
- *Patch:* `840fdbd` phrase-aware boost, `ac1bfae` trim SEARCH_PRODUCTS to dominant winner.
- *Why:* Search side narrowed — but `add_to_cart` still doesn't enforce "tool refuses if no catalog ID resolves." Architectural gap (see "Tools: hard contracts" below).

**T-4 · +573159280840 (dev) · "no tengo precios" after order placed**
- *Component:* Order agent — Response generator + Executor lost cart context post-PLACE_ORDER.
- *Patch:* `711c79c` cart-total questions, per-item breakdown, in-cart GET_PRODUCT.
- *Why:* Added explicit GET_PRODUCT path that reads from `order_context` after PLACE_ORDER.

**T-4 · +573159280840 (dev) · Menu link "Lo siento, no tengo"**
- *Component:* CS agent — Planner didn't know `menu_url` was a field it could surface.
- *Patch:* `6bce15d` route 'carta' requests to menu_url field.
- *Why:* New CS rule: any `carta`/`menú` token → return `business.settings.menu_url` verbatim.

**T-5 · +573107372328 · Bot couldn't surface Nequi info post-order**
- *Component:* CS agent — Planner wasn't reading `business.settings.payment_details`.
- *Patch:* `cda0496` surface business ai_prompt rules in CS agent responses.
- *Why:* CS planner now joins `business.settings` into its system prompt.

**T-9 · +573116994918 · Two bot replies in same turn (race)**
- *Component:* Dispatcher / Order agent — Planner. Single-intent loop fired both an ADD_TO_CART reply and a clarification reply.
- *Patch:* `056ccea` multi-intent planner + order-confirm CTA + UX guards.
- *Why:* Multi-intent planner batches intents into one turn and produces ONE composed reply. (This fix introduced the CONFIRM dedup hack discussed in the deep dive.)

**T-9 · +573116994918 · Pickup not first-class**
- *Component:* Schema / Order tools.
- *Patch:* `10eadc4` attempted, reverted by `28e9258`.
- *Why:* Reverted because the pickup flag broke confirmation copy. **Still open.**

**T-10 · +573122967295 · Single-word "Si" lost context**
- *Component:* Order agent — Planner + Conversation manager.
- *Patch:* `8ef5496` single-word product short-circuit, greeting+product prompt rule.
- *Why:* Planner now has a short-circuit: if message length ≤ 1 token AND is a confirmation/affirmation, resolve via state, don't re-classify.

**T-11 · +573223351744 · RAMONA missing from catalog**
- *Component:* Catalog data — product not yet entered.
- *Patch:* No code patch (operations); `9286c5a` ensures future admin writes regenerate tags+embeddings.
- *Why:* The fix is the workflow: when admin enters a product, embeddings/tags rebuild automatically so the bot can find it on the next turn.

**T-12 · +573173187263 · Bot returned business contact for Nequi**
- *Component:* CS agent — Planner.
- *Patch:* `cda0496` (same as T-5).
- *Why:* Same fix path — `payment_details` now surfaced.

**T-14 · +573178694096 · Bot took order while restaurant closed**
- *Component:* Router — missing availability gate.
- *Patch:* `bfccba0` (Thu) order-availability gate + closed-state welcome.
- *Why:* Router now consults `business_availability` before letting any cart-mutating intent through.

### Wed 2026-05-06

**W-1 · +573137112249 · "Hamburguesa" → ghost cancel**
- *Component:* Order agent — Planner + Cancel keywords matched too aggressively.
- *Patch:* `8ef5496` single-word short-circuit; `7771e35` harden destructive intents.
- *Why:* Single-word product names now route to GET_PRODUCT before reaching the destructive-intent classifier. Destructive intents require a verb form, not just a keyword.

**W-1 · +573137112249 · Operator pasted #3ACB4460 (another customer's ID)**
- *Component:* Admin panel — missing "Crear pedido".
- *Patch:* `483b36b` (Thu).
- *Why:* See T-3.

**W-2 · +573177871235 · "5 minutos" ETA invented**
- *Component:* Order agent — Response generator hallucinated. No order-status lookup tool exists.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Architectural gap: the response generator is allowed to compose ETA strings without a tool returning real data. Need an `order_status_lookup` tool returning `{status, real_eta_minutes}` and a rule "ETA strings only from this tool."

**W-2 · +573177871235 · "mostaneza" stored typo**
- *Component:* Catalog data.
- *Patch:* Editorial fix needed.
- *Why:* Not a code bug.

**W-3 · +573108473692 · Two bot replies same turn (race)**
- *Component:* Dispatcher / Order agent — Planner.
- *Patch:* `056ccea` (same as T-9).
- *Why:* Multi-intent planner.

**W-4 · +573042339633 · Inconsistent promo replies vs. W-3**
- *Component:* CS agent — Planner non-deterministic on promo phrasing.
- *Patch:* `cda0496` partial; deeper fix open.
- *Why:* CS planner needs to read `business.settings.promotions` deterministically and template the reply.

**W-6 · +573206606089 · `[menú de Biela](menu_url)` literal leak**
- *Component:* Order agent — Response generator template, unsubstituted variable.
- *Patch:* `6bce15d` covers most paths; this code path missed.
- *Why:* Template substitution should be enforced by the executor (`result_kind=menu_link` returns the resolved URL; response generator only renders), not by the response LLM. **Open.**

**W-8 · +573134700128 · "Ahí está perfecto gracias" not recognised as CONFIRM**
- *Component:* Order agent — Planner confirmation-phrase recognition.
- *Patch:* `056ccea` multi-intent planner; `63d1933` state-aware SUBMIT/CONFIRM dedup.
- *Why:* This is THE conversation that triggered the multi-intent CONFIRM dedup hack. See "Multi-intent + CONFIRM dedup" in the deep dive.

**W-8 · +573134700128 · "no manejamos cambio" / "tarjeta" hallucinated**
- *Component:* CS agent — Response generator invented business policies.
- *Patch:* Partial — `cda0496` surfaces business config; deeper fix open.
- *Why:* Response generator must be forbidden from making policy claims not in `business.settings.ai_prompt`.

**W-8 · +573134700128 · "15-20 min" ETA fabricated**
- *Component:* Order agent — Response generator.
- *Patch:* **NOT YET PATCHED** (same as W-2).
- *Why:* Same gap.

**W-8 · +573134700128 · No `order_status_lookup` tool when customer asked**
- *Component:* Order agent — Tools, missing.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Customer's "Cómo verifico el estado de mi pedido?" had nowhere to land. Need the tool.

**W-9 · +573164991471 · `menu_url` placeholder leak**
- *Component:* Response generator template.
- *Patch:* Same as W-6.
- *Why:* **Open.**

**W-9 · +573164991471 · Pickup intent acknowledged but not flagged**
- *Component:* Schema gap.
- *Patch:* Same as T-9.
- *Why:* **Open** (revert).

**W-10 · +573226281785 · 3 separate prompts for 3 missing fields**
- *Component:* Order agent — Planner / Executor sequential slot filling.
- *Patch:* `056ccea` partial — multi-intent helps, but executor's "ask for missing delivery info" still iterates field-by-field.
- *Why:* Should ask for ALL missing fields once. **Open.**

**W-12 · +573152188233 · "Sin papas" pricing not modelled**
- *Component:* Catalog data — modifications don't change price.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Schema needs price-by-modifier or a fixed "no discount on removals" rule.

**W-12 · +573152188233 · Two bot replies same turn**
- *Component:* Dispatcher / Planner.
- *Patch:* `056ccea` — race still appearing post-deploy.
- *Why:* Indicates `056ccea` doesn't cover all entry paths.

**W-13 · +573015349690 · Multi-line message ignored (only product extracted)**
- *Component:* Order agent — Planner can't parse mixed product-request + delivery info in one message.
- *Patch:* **NOT YET PATCHED.**
- *Why:* This is your "real life copy-paste" pain point. Needs a pre-planner extraction layer (entity tagger that pulls name/address/phone/payment fields BEFORE the planner runs).

**W-13 · +573015349690 · Cart mutated after PLACE_ORDER (complaint→edit)**
- *Component:* Order agent — Executor + State machine doesn't freeze cart in PLACED state.
- *Patch:* **NOT YET PATCHED.**
- *Why:* `order_status_machine.py` exists but doesn't gate `add_to_cart` / `remove_from_cart` calls when state is PLACED. Architectural fix.

**W-13 · +573015349690 · Bot defended a charge instead of escalating**
- *Component:* CS agent — missing complaint-handling intent.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Need a "post-delivery complaint" CS intent that opens a ticket / pings operator.

**W-15 · +573246206322 · Promo reply inconsistent**
- *Component:* Same as W-4.
- *Patch:* Same.
- *Why:* Same.

### Thu 2026-05-07

**Th-3 · +573155911909 · Citizen ID accepted as phone**
- *Component:* Order agent — Executor / Tools, missing field validation.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Phone field needs format validation before `place_order` accepts it. Open.

**Th-3 · +573155911909 · "5 minutos" ETA invented**
- *Component:* Same as W-2.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Same.

**Th-4 · +573247084245 · Admin cancel doesn't capture reason**
- *Component:* Admin panel UX.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Cancel button should require a reason field.

**Th-4 · +573247084245 · Customer not notified of admin cancel**
- *Component:* Admin panel + outbound notifier.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Cancel action should fire a customer message via WhatsApp.

**Th-4 · +573247084245 · No "reactivate" button → DB diverges from reality**
- *Component:* Admin panel UX.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Either reactivate, or "uncancel" by creating a new order linked to the cancelled one.

**Th-5 · +573242739292 · Planner over-disambiguated despite single-tag-match**
- *Component:* Order agent — Planner disambiguation rule too eager.
- *Patch:* **NOT YET PATCHED.**
- *Why:* When `SEARCH_PRODUCTS` returns one product whose tag explicitly matches the qualifier (`burger_master`), planner should ADD_TO_CART directly. Currently it asks regardless. Architectural scope: ~1 line in the planner's tool-result handling.

**Th-5 · +573242739292 · Debouncer didn't widen for typing burst**
- *Component:* Debouncer — fixed-window.
- *Patch:* **NOT YET PATCHED.**
- *Why:* `debounce.py` has abort + coalesce but no adaptive widening. Fix shape: when a new message arrives during the wait window, multiplicatively extend (cap ~8s); on sub-second cadence treat as a typing storm and wait for a real silence (~3s). Deterministic — no LLM needed.

**Th-5 · +573242739292 · Order placed without burger**
- *Component:* Order agent — Planner multi-intent ran PLACE_ORDER while ADD_TO_CART for the burger was still pending.
- *Patch:* Same family as W-8 — multi-intent CONFIRM dedup hack.
- *Why:* The dedup hack (CONFIRM stripped from cart-mutation arrays) prevents the inverse bug (placing while modifying), but not this case (intent dropped silently). See deep dive.

**Th-5 · +573242739292 · Phone field with duplicated digits**
- *Component:* Executor — missing validation.
- *Patch:* Same as Th-3.
- *Why:* Open.

**Th-5 · +573242739292 · Operator pasted bot template**
- *Component:* Admin panel.
- *Patch:* `483b36b`.
- *Why:* See T-3.

**Th-6 · +573104078032 · "cancelo al domiciliario" misread as cancel**
- *Component:* Cancel keywords + Order agent — Planner.
- *Patch:* `7771e35` partial — hardened destructive intents, but `cancel_keywords.py` is still substring-match.
- *Why:* Need to replace substring matching with planner-LLM intent classification, OR add disambiguation: "cancelar" without "el pedido"/"la orden" requires confirmation.

**Th-6 · +573104078032 · Two bot replies same turn (twice)**
- *Component:* Dispatcher / Planner.
- *Patch:* `056ccea` not covering all paths.
- *Why:* **Open.**

**Th-6 · +573104078032 · Operator pasted #409A800F (another customer's ID)**
- *Component:* Admin panel.
- *Patch:* `483b36b`.
- *Why:* See T-3.

**Th-9 · +573502736889 · "alitas de res" silently substituted to burgers**
- *Component:* Catalog / Search — relevance threshold too loose.
- *Patch:* `840fdbd`, `ac1bfae` partial — still happens in this case.
- *Why:* `SEARCH_PRODUCTS` returns top-K even when none score well. Need a hard relevance floor; below floor → return empty / `ProductNotFoundError`.

**Th-10 · +573205649881 · Closed status not propagated to CS turns**
- *Component:* Router / CS agent — closed flag set on greeting but not on follow-ups.
- *Patch:* **NOT YET PATCHED.**
- *Why:* Router should set a `business_open` flag in `turn_context` that all agents read.

---

## Order agent deep dive — what's brittle, what to redesign

You're rebuilding this. Here's where the Tue→Thu pain landed in concrete terms.

### Planner: the 3 modes it's bad at

The planner emits intents from a fixed enum. Three patterns produced ~80% of incidents:

**1. Single-word / short-message classification** (T-10 "Si", W-1 "Hamburguesa", Th-6 "cancelo")

The planner doesn't have stable behaviour for messages of length ≤ 2 tokens. They get over-classified (W-1 "Hamburguesa" → CANCEL) or wipe context (T-10 "Si" → GREETING). Patch `8ef5496` added a short-circuit, but it's an *exception path* on top of the planner, not a redesign.

**Redesign suggestion**: a deterministic pre-classifier that runs BEFORE the planner LLM:
- Length ≤ 1 token → look up state, route based on what was asked.
- Pure-affirmation lexicon ("si", "sí", "ok", "dale", "claro", "listo") + state == AWAITING_CONFIRMATION → emit CONFIRM directly without LLM.
- Pure-negation lexicon + state == AWAITING_CONFIRMATION → emit ABANDON_CART directly.
- Catalog lookup match (exact or near) → emit GET_PRODUCT.
Only if none match does the LLM see it.

This makes the LLM's job "interpret natural language", not "decide between 16 intents on a one-token input."

**2. Multi-intent + CONFIRM dedup** (W-8 "Ahí está perfecto gracias", Th-5 burger missing, the user's debugging pain)

The user described this directly: the planner kept adding CONFIRM to multi-intent arrays alongside cart-mutation intents (e.g. `[ADD_TO_CART(RAMONA), CONFIRM]`). This breaks the executor's state machine because PLACE_ORDER fires before the cart mutation completes. The current fix is a deterministic post-process that strips CONFIRM from any intent array containing a cart-mutation intent — patched but admittedly hacky.

The root cause: **the planner has no way to know "the cart isn't ready yet"** because it sees `order_context` as a snapshot from BEFORE its own intents apply. It's classifying the user's message in isolation, then the executor has to play catch-up.

**Redesign suggestion**: split planning into two phases.
- Phase 1 — *intent extraction* (LLM): "What does the user want? Output a list of atomic intents with no ordering or commitment."
- Phase 2 — *intent scheduling* (deterministic): take the intent list, the current state, and the cart, and produce the actual transition sequence. This phase is where "you can't CONFIRM if there's a pending cart mutation" lives — as a rule, not as a post-hoc strip.

This turns "the planner emits a transition" into "the planner emits a desire, and the scheduler decides when to act on it." The CONFIRM dedup goes away because the scheduler simply won't schedule a PLACE_ORDER until the cart is in READY_TO_PLACE.

**Bonus**: Phase 1 becomes way easier to prompt because the LLM doesn't have to know the state machine. It just has to enumerate what the user wants.

**3. Disambiguation triggered when not needed** (Th-5)

The Burger Master case: customer wrote "hamburguesa del burger master con papas + coca cola zero", catalog had RAMONA tagged `burger_master`, planner asked anyway. The planner doesn't consult the *strength* of the search match; it disambiguates whenever there are multiple-ish candidates.

**Redesign suggestion**: SEARCH_PRODUCTS should return `{candidates: [...], strength: float}` where strength is 1.0 for exact catalog name match, 0.95+ for unique tag match, and degrades from there. The planner should auto-add when strength ≥ 0.9 and ask only below that threshold. **One-line change in the planner's tool-result handling.**

### Executor: the state machine isn't enforced

`order_status_machine.py` defines the states (GREETING → ORDERING → COLLECTING_DELIVERY → READY_TO_PLACE → PLACED → COMPLETED/CANCELLED). But the cart-mutation tools (`add_to_cart`, `remove_from_cart`) don't gate on state. Result: W-13 (+573015349690) had cart mutated AFTER PLACE_ORDER because the customer complained about a missing item and the planner emitted REMOVE_FROM_CART.

**Redesign suggestion**: every tool declares the states it accepts. The executor rejects tool calls outside those states with a typed error result the planner can react to. This is a 30-line change, but it kills an entire class of bugs.

### Tools: hard contracts

Currently `add_to_cart` raises `AmbiguousProductError` / `ProductNotFoundError` as Python exceptions, which the planner catches and treats as flow-control. T-4 (+573159280840 dev) shows the worst of this: when no exception fires but the resolver returns garbage (LA VUELTA, MANHATTAN), the tool happily inserts a phantom line.

**Redesign suggestion**: tools return `{ok: bool, candidates|error|resolved_id, hint}`. Never throw. The planner reads `ok` and decides. The tool *itself* refuses to write when `ok=false`. This means:
- No more "phantom product order" because `add_to_cart` literally cannot insert without a resolved ID.
- No more "AmbiguousProductError stack trace in logs" because ambiguity is normal flow control.

### Response generator: stop letting it narrate outcomes

The hallucinated ETAs (W-2, W-8, Th-3 "5 minutos", "15-20 min") all come from the response generator inventing strings the executor never produced. Same shape as the operator template-paste anti-pattern, but inside the system.

**Redesign suggestion**: every customer-facing string with numerical/factual content (order ID, total, ETA, address recap) must be produced verbatim by the executor. The response generator gets a typed payload and a "wrap with a friendly opener" instruction — it cannot author the numbers themselves. This kills hallucination at the source.

For non-factual replies (greetings, follow-ups, soft-skill recovery), let the response LLM be free.

### Debouncer: typing-storm aware

`debounce.py` has the right primitives (abort + coalesce + wait) but the wait is fixed-short. Th-5 is the proof: customer in a rapid burst, planner ran while customer was still typing the disambiguation answer.

**Fix shape (deterministic, no LLM)**:
- Default wait: current short value (presumably 1-2s).
- Each new message arriving during the wait extends the wait by a multiplicative factor (e.g. ×1.5, capped at 8s).
- Sub-second inter-message cadence triggers "typing storm" mode: wait for 3s of silence before firing.
- Hard timeout at 10s regardless.

This belongs in the debouncer, not the planner. Don't ask the LLM to figure it out.

---

## Summary: which patches actually shipped this week, by component

| Component | Patches landed | Open architectural items |
|---|---|---|
| **Router** | `bfccba0` (closed gate), `b07c78b` (greeting domain), `a150a1a` (stuck-article splitter) | Closed-state propagation to follow-up turns. Pre-planner entity extraction for multi-line messages. |
| **Conversation manager** | `636dc10` (uniform 10-msg history), `7771e35` (operator-turn tagging start), `d64455b` (wa_id+turn_id tracing) | Operator-turn tagging full coverage. |
| **Order agent — Planner** | `7ec4aa2`, `056ccea`, `8ef5496`, `7ec4aa2`, `7771e35`, `63d1933`, `4435c40`, `0944365` | **Phase 1/Phase 2 split for multi-intent**, **deterministic short-message pre-classifier**, **strength-aware disambiguation rule**. |
| **Order agent — Executor** | `bfccba0`, `056ccea` (CTA + UX guards) | **State-machine enforcement on all tools**, **post-PLACE_ORDER cart freeze**. |
| **Order agent — Tools** | `82cc282` (no crash on search), `840fdbd`, `ac1bfae` | **Tools return `{ok, ...}` instead of raising**, **`add_to_cart` refuses without resolved ID**, **`order_status_lookup` tool**, **field validation on phone**. |
| **Order agent — Response generator** | `711c79c`, `cda0496`, `9ed94a0` (delivery-fee variance) | **Forbid LLM from authoring factual strings** (order ID, total, ETA), **template-substitution moved to executor**. |
| **CS agent** | `66329dc`, `6bce15d`, `cda0496`, `4435c40`, `986fb92`, `4bb3338` | **Deterministic promo reply from `business.settings.promotions`**, **complaint intent for post-delivery issues**. |
| **Catalog / Search** | `840fdbd`, `ac1bfae`, `9286c5a`, `82cc282` | **Hard relevance floor (no top-K when none score well)**, **search returns `{candidates, strength}`**. |
| **Cancel keywords** | `7771e35` (hardened) | **Replace substring matching with intent classification**, or context-aware: "cancelar" without "el pedido" requires confirmation. |
| **Admin panel** | `483b36b` (Crear pedido), `c8c6b16` (thermal print + edit), `d7f32f7` (status changes), `aa84b62` (customer profile), `32bd17c` (per-business customers) | **Cancel requires reason**, **cancel fires customer notification**, **reactivate / amend flow**. |
| **Debouncer** | (none this week) | **Adaptive widening + typing-storm mode**. |

The order-agent column has the longest open-items list, which matches your read. The two highest-leverage refactors there are (a) Phase 1/Phase 2 split for multi-intent — kills the CONFIRM dedup hack and the burger-missing case, and (b) typed `{ok, ...}` tool returns + state-machine enforcement — kills phantom products, post-PLACE_ORDER cart mutations, and silent substitutions.
