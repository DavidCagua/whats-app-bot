# Order Agent: Multi-Item Add & Response Continuation — Analysis

## 1. Current planner/executor behavior

### Planner (`order_agent.py`)

- **Output**: Single JSON `{"intent": "ADD_TO_CART", "params": {...}}` per message.
- **ADD_TO_CART rule**: "Si pide agregar algo: ADD_TO_CART con product_name/product_id y quantity."
- **Params today**: Only single-product shape: `product_name`, `product_id`, `quantity` (default 1). No `items` array.
- **Parsing**: `_parse_planner_response` returns one intent and one params dict; no special handling for multiple items.

### Executor (`order_flow.py`)

- **ADD_TO_CART handling** (lines 223–227): Builds a single `tool_args` from `params`: `product_id`, `product_name`, `quantity`.
- **Invocation**: One `tool_fn.invoke(tool_args)` per intent (line 284). No loop.
- **State transition** (313–318): After one successful add (result contains "✅") and state was GREETING → set state to ORDERING.
- **Result**: Single string `tool_result` and single `cart_summary` returned to the response generator.

### Tool `add_to_cart` (`order_tools.py`)

- **Signature**: `add_to_cart(product_id="", product_name="", quantity=1, injected_business_context=...)`. Single product only.
- **Behavior**: Loads cart from session, adds/updates one product, saves cart. Session is keyed by `wa_id` + `business_id`, so sequential calls see the updated cart. Safe to call in a loop.

---

## 2. Proposed schema changes for ADD_TO_CART

- **New format (multi-item)**:  
  `params: { "items": [ { "product_name": "MONTESA", "quantity": 1 }, { "product_name": "BOOSTER", "quantity": 1 } ] }`  
  Optional per item: `product_id` if known.

- **Legacy format (single item)**:  
  `params: { "product_name": "X", "quantity": 1 }` or `"product_id": "uuid"`.

- **Resolution**: If `params` has `"items"` and it is a non-empty list, use multi-item path. Otherwise use existing single-item path (`product_name` / `product_id` + `quantity`). No change to session schema or tool function signatures.

---

## 3. Changes required in executor logic

- **When intent == ADD_TO_CART**:
  - **If `params.get("items")` is a non-empty list**:
    - Optionally log cart_before once.
    - For each entry in `params["items"]`: build `tool_args` from `item.get("product_name")`, `item.get("product_id")`, `item.get("quantity", 1)`, invoke `add_to_cart`, append the tool result string to a list.
    - Concatenate all results (e.g. newline-separated) into a single `tool_result` string.
    - After the loop, optionally log cart_after once (cart debug).
    - If at least one result contains "✅" and `current_state == ORDER_STATE_GREETING`, run the same state transition as today (save ORDERING).
    - Return `success = True` if no result contains "❌", else partial success; `tool_result` = concatenated string; `cart_summary` = from session after the loop.
  - **Else**: Keep current behavior: single `add_to_cart` call with `params["product_name"]`, `params["product_id"]`, `params["quantity"]`.

- **Cart debug**: For multi-item, log once before the loop and once after (cart_before / cart_after for the whole batch).

---

## 4. Prompt improvement for conversational continuation

- **Where**: `RESPONSE_GENERATOR_SYSTEM` in `order_agent.py`.
- **Add**: After a successful ADD_TO_CART (one or more items), the assistant must: (1) confirm what was added, (2) show the updated cart summary, (3) suggest the next step: e.g. "¿Deseas agregar algo más (ej. bebida)? ¿O procedemos con el pedido?"
- **Constraint**: Do not claim cart changes that were not confirmed by the backend; only describe what is in the provided `tool_result` and `cart_summary`.

---

## 5. Summary

| Area            | Change                                                                 |
|-----------------|------------------------------------------------------------------------|
| Planner prompt  | Document ADD_TO_CART with `items: [{ product_name, quantity }]` or legacy single product. |
| Executor        | If `params.items` present and non-empty, loop add_to_cart; aggregate results; one state transition and cart debug. Else unchanged. |
| Tool            | No signature change; used repeatedly by executor.                       |
| Response prompt | Add rule: after successful ADD_TO_CART, confirm + cart + suggest next step. |
