# Agents vs. Services: how to organize multi-agent code

**Status**: principle, not a phase plan. Applies to every future agent and every router-prompt change.

## The principle

> **Agents are organized around USER CONCERNS. Services are organized around OPERATIONS. Agents COMPOSE services to serve their concern.**

A user concern is something a human is *trying to do* — not something the bot is *technically able to do*. The user doesn't think "I want to call SEARCH_PRODUCTS." They think "I want to order food."

A service is a reusable piece of capability: a database read, an API call, a search. Services don't know which agent is calling them. They just answer what they're asked.

When these get conflated, the architecture starts to drift in ways that look reasonable but produce real bugs in production. This doc records the principle and the production lesson that taught us to keep it.

## What goes where

| User concern | Agent | Services it composes |
|--------------|-------|---------------------|
| "I want to order food" | **order** | `catalog_service` (browse, search, details), `product_order_service` (cart, checkout) |
| "I want info about the business" | **customer_service** | `business_info_service` (hours, address, menu URL, ...), `order_lookup_service` (status, history) |
| "I want to book a table" | **booking** | (booking service, when enabled) |
| "I want to be marketed to" | **marketing** *(future)* | `catalog_service`, customer memory, ... |

Services are technical capabilities, agnostic of agent:

| Service | Used by |
|---------|---------|
| `catalog_service` | order today; a future marketing agent could reuse it for product suggestions |
| `business_info_service` | customer_service today; the router could call it directly for fast-paths |
| `order_lookup_service` | customer_service today |
| `business_greeting` | router today (greeting fast-path) |
| `product_order_service` | order today (cart writes, checkout) |

The service boundary is about **reusability**. The agent boundary is about **what concern the user is trying to accomplish**.

## The mistake we made: `catalog` as a router domain

For most of the multi-agent rollout, the router emitted four domains:

```
order | customer_service | catalog | chat
```

`catalog` was meant to capture "the user is asking about products / the menu." On paper that looks fine. In practice it was a category mistake — `catalog` is **an operation**, not a concern. It belongs at the service layer, not the agent-routing layer.

The damage showed up as soon as we got mixed-intent traffic. Two real production traces:

### Symptom 1 — overload across two unrelated concerns

User said: **"me envia la carta y me da una barracuda"** ("send me the menu and give me a barracuda").

The router classified `me envia la carta` as `catalog`. But "envíame la carta" is not browsing — the user wants the **menu URL** (an asset), same shape as "envíame la dirección" or "cuál es el teléfono." That's a business-info concern, not a browse-the-bot concern.

Because `catalog` was a domain, the prompt happily lumped both meanings into it. The router couldn't disambiguate "I want to browse the menu in the bot" from "I want you to send me the menu URL." Same domain, different concerns, different agents would handle them.

### Symptom 2 — fall-through coupling

`catalog` had no dedicated agent so the conversation_manager mapped it to the primary agent (order). After segment coalescing, `[("order", "barracuda"), ("order", "send me the menu")]` collapsed into one order-agent call, the planner emitted ONE intent (`ADD_TO_CART`), and the menu request was silently dropped.

We patched that by coalescing only on `(domain, agent_type)` instead of `agent_type` alone. That fixed the second symptom but the first symptom — wrong agent owning the URL request — remained until we removed the domain entirely.

### Why it kept biting

Because `catalog` was on the agent-routing layer, every refinement felt like patching the wrong thing:
- "Make the router prompt smarter about envíame vs muéstrame" — patches a leaky abstraction.
- "Make LIST_PRODUCTS responses include the URL" — duplicates URL logic the customer_service agent already has.
- "Add a new dedicated catalog agent" — fragments a single concern (ordering) across two agents.

None of those are wrong as engineering. They're all wrong as **architecture**. The actual fix is one tier up: drop the domain and split its meaning by user concern.

## The fix we landed on

Three router domains, organized by concern:

```
order             # "I want to order food" — includes browsing the menu in the bot
customer_service  # "I want info about the business" — includes the menu URL as an asset
chat              # fallback / small talk
```

Disambiguator at the router prompt level:

| User says | Domain | Why |
|-----------|--------|-----|
| "qué bebidas tienen" | `order` | Browsing inside the bot, in service of ordering |
| "muéstrame el menú" | `order` | Same |
| "tienen coca cola?" | `order` | Asking about a product (browse) |
| "envíame la carta" | `customer_service` | Wants the URL ASSET — verb of send/share + link/menu as object |
| "pásame el link del menú" | `customer_service` | Same |
| "me das el teléfono" | `customer_service` | Asset request — phone |
| "tienen domicilio?" | `customer_service` | Policy question, not a product |
| "cuánto cobran de domicilio?" | `customer_service` | Policy / asset |
| "dónde está mi pedido" | `customer_service` | Status — post-sale concern |
| "dame una barracuda" | `order` | Cart action |
| "ya te pago" / "listo" | `order` | Checkout signal |

The verb is often the disambiguator: **send / share / pass + (link/carta/menu as direct object)** is a customer_service asset request. **Show / have / want + product/category** is an order browsing action.

## How to decide where a future intent goes

When you're tempted to add a new domain to the router, or a new agent to the registry, run this checklist:

1. **Is this a USER CONCERN, or a TECHNICAL OPERATION?**
   - Concern → agent boundary.
   - Operation → service boundary.

2. **Could two different agents reasonably need this capability?**
   - Yes → service. Don't make it an agent.
   - No → it's specific to one concern; live there.

3. **Does the user care about this in isolation, or only in service of another concern?**
   - In isolation (e.g. "what are your hours?") → top-level concern, goes to its own agent.
   - Only as a sub-step (e.g. "what bebidas do you have?" while ordering) → sub-step of an existing concern, lives in that agent.

4. **What verb does the user use?**
   - "Tell me about / send me / share with me / what is" + asset → information request (customer_service).
   - "Show me / list / what do you have" + product/category → browse (order, since browse-to-order is one funnel).
   - "Give me / I want / add to cart" + product → action (order).

5. **Will the user's tone/expectation in the response change based on the surrounding turn?**
   - Yes → keep it in the same agent that handles the surrounding turn (cohesion).
   - No → can be a peer agent.

## Why we did NOT extract catalog as its own agent

It's tempting to apply Single-Responsibility hard and create a `catalog_agent`. We considered it. The reasoning we used to NOT do it is worth recording so future revisits don't re-debate from scratch.

**Costs of extracting catalog into its own agent:**
- **Loses turn cohesion.** "Qué bebidas tienen?" → catalog → "una coca" → order. Each pair of turns now does two router classifications + agent switches. Latency up, classifier accuracy must stay high.
- **Tone fragmentation.** Browse responses ("aquí están las bebidas, ¿cuál te provoca?") and cart confirmations ("listo, agregué") are stylistically conjoined parts of one conversation. Two response generators trying to maintain one voice ends in disjointed merging.
- **Loses planner context.** Order's planner sees the previous turn's product list and correctly classifies "una" or "una coca" as ADD_TO_CART. A separate catalog agent doesn't write to `order_context`, so order has to re-derive context from raw conversation history — slower and error-prone.

**Benefit of extracting catalog into its own agent:**
- Cleaner separation on paper.

The cleanness benefit is **already captured** by `catalog_service.py` being its own module, callable by any agent. The agent boundary doesn't need to mirror the service boundary.

## Anti-patterns to flag in code review

If you see any of these in a PR, the architecture is drifting:

- **"Let's add a new domain to the router."** Don't, until you've answered: is this a user concern or a technical operation? If operation, it's a service.
- **"Both agents need this capability — let's add it to both."** Don't, extract a service.
- **"This intent could go in either agent — let's put it in both for safety."** Don't, pick one based on user concern, write a handoff path for the edge case.
- **"The router prompt is getting big — let's split a domain off."** Often a signal you've conflated a concern with an operation. Look for an operation hiding inside a concern.

## When to revisit this principle

Concrete signals that justify breaking the principle:

- **A capability needed by 3+ agents** — at that scale, a service may not be enough; a dedicated agent that other agents hand off to could be cleaner. We're nowhere near this today.
- **A user concern that doesn't map to any existing agent** — e.g. "I want to give feedback / leave a tip / claim a refund." That's a real new concern. New agent.
- **Cross-tenant federation** — if the same business runs both a Biela-style fast-food bot and a barber-shop bot, some concerns split per vertical. Different problem; revisit then.

Until then: agents follow concerns, services follow operations, and the router classifies by what the user is *trying to do*.

## Reference: the production trace that taught us this

**Date**: 2026-04-25.

User sent: "me envia la carta" then (mid-processing) "y me da una barracuda".

Trace:
1. Router classified the coalesced text into `[catalog, order]`.
2. `catalog` had no dedicated agent → fell back to order (primary).
3. Conversation_manager built `[(order, "carta"), (order, "barracuda")]`.
4. Coalesce-by-agent merged into `[(order, "carta + barracuda")]`.
5. Order's planner could only emit one intent → picked `ADD_TO_CART(barracuda)`.
6. The menu URL request was silently lost.

Two patches followed:
- **First patch** (correctness): coalesce by `(domain, agent_type)` not just `agent_type`. Distinct user intents that fall back to the same agent stay as separate dispatcher calls.
- **Second patch** (architecture): drop `catalog` from the router domain set. "Envíame la carta" classifies as customer_service (asset request). "Qué hay en el menú" classifies as order (browse-to-order). The first patch is still useful but the second prevents the issue from arising at all.

The lesson: the coalesce fix was a real bug fix at the dispatcher level. The architectural fix was a category correction at the router level. Both were necessary, but only the architectural fix prevents a class of bugs we'd otherwise keep patching one phrasing at a time.
