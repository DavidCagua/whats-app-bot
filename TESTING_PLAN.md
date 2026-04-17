# Testing Plan — Order Agent

Progressive testing strategy for the restaurant order-taking agent, based on [LangChain testing docs](https://docs.langchain.com/oss/python/langchain/test) and [Anthropic's evals guide](https://www.anthropic.com/research/building-effective-agents).

---

## Architecture Context

The order agent follows a **3-stage pipeline**:

```
User Message → Planner (LLM) → Executor (deterministic) → Response Generator (LLM)
```

- **Planner**: Classifies intent (13 types) + extracts params via `gpt-4o-mini`
- **Executor**: Runs tools, manages state transitions (`order_flow.py`)
- **Response Generator**: Produces Spanish response from backend result via LLM

**Order States**: `GREETING → ORDERING → COLLECTING_DELIVERY → READY_TO_PLACE → GREETING`

---

## File Structure

```
tests/
├── conftest.py                # Shared fixtures: test DB, mock services, fake LLM, API key checks
├── unit/
│   ├── test_order_flow.py     # State machine transitions & guards
│   ├── test_order_tools.py    # Tool functions in isolation
│   ├── test_parsing.py        # Planner response parsing
│   └── test_pipeline.py       # Full pipeline with GenericFakeChatModel
├── integration/
│   ├── test_planner.py        # Intent classification with real LLM
│   ├── conftest.py            # VCR config, API key validation
│   └── cassettes/             # Recorded HTTP interactions
├── e2e/
│   └── test_scenarios.py      # Full conversation flows (multi-turn)
└── evals/
    ├── test_regression.py     # Graduated passing tests (should stay ~100%)
    └── test_capability.py     # Stretch/edge-case tests (expect low pass rate)
```

---

## Phase 1: Unit Tests

**Goal**: Test all deterministic logic without API calls. Fast, free, repeatable.

### 1a. Order Flow State Machine (`test_order_flow.py`)

Test `execute_order_intent()` and `derive_order_state()` with mocked tool functions.

**State transitions to verify:**
- `GREETING` → first `ADD_TO_CART` success → `ORDERING`
- `ORDERING` → `PROCEED_TO_CHECKOUT` → `COLLECTING_DELIVERY`
- `COLLECTING_DELIVERY` → all delivery info present → `READY_TO_PLACE`
- `READY_TO_PLACE` → `PLACE_ORDER` success → `GREETING` (context cleared)

**Guards to verify:**
- Can't `PLACE_ORDER` from `GREETING`
- Can't `PROCEED_TO_CHECKOUT` with empty cart
- Can't `PLACE_ORDER` with incomplete delivery info
- Menu browsing intents work from any state

### 1b. Tool Functions (`test_order_tools.py`)

Test each tool in isolation with a test DB or mocked DB services.

| Tool | What to test |
|------|-------------|
| `add_to_cart` | Adds item, calculates total, handles duplicates, fuzzy name match |
| `update_cart_item` | Fuzzy name matching, notes update, quantity change |
| `remove_from_cart` | Removes correct item, recalculates total |
| `view_cart` | Returns correct summary from order_context |
| `search_products` | Token-based matching, stopword skipping, ingredient search |
| `list_category_products` | Category normalization, fallback behavior |
| `get_customer_info` | Merges session delivery_info with DB customer record |
| `submit_delivery_info` | Partial updates, field validation, completeness check |
| `place_order` | Creates Order + OrderItems in DB, validates completeness, clears context |

### 1c. Planner Response Parsing (`test_parsing.py`)

Test `_parse_planner_response()` with various LLM output formats:
- Clean JSON: `{"intent": "ADD_TO_CART", "params": {...}}`
- JSON in markdown code block
- JSON with extra whitespace/newlines
- Malformed/incomplete JSON (error handling)

### 1d. Full Pipeline with Fake LLM (`test_pipeline.py`)

Use LangChain's `GenericFakeChatModel` to script both the planner and response generator responses. This tests the full `OrderAgent.execute()` 3-stage pipeline deterministically.

```python
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

fake_llm = GenericFakeChatModel(messages=iter([
    # Planner response
    AIMessage(content='{"intent": "ADD_TO_CART", "params": {"items": [{"product_name": "barracuda", "quantity": 1}]}}'),
    # Response generator
    AIMessage(content="✅ Listo, agregué 1 Barracuda a tu pedido."),
]))
```

**What this covers:**
- Planner → Executor → Response Generator wiring
- Order context mutations flow through correctly
- State updates propagate between stages

---

## Phase 2: Integration Tests — Planner

**Goal**: Verify the planner LLM correctly classifies intent from real Spanish messages.

### Setup

```python
# pytest.ini
[pytest]
markers =
    integration: tests that call real LLM APIs
addopts = -m "not integration"
```

```python
# tests/integration/conftest.py
import os, pytest

@pytest.fixture(autouse=True)
def check_api_keys():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
```

### LangChain Tools Used

- **`@pytest.mark.integration`** — Separate from unit tests, run with `pytest -m integration`
- **`pytest-recording` (VCR)** — Record/replay HTTP calls to cut API costs in CI
- **Structural assertions** — Assert on intent name + param keys, not exact response text

### VCR Configuration

```python
# tests/integration/conftest.py
@pytest.fixture(scope="session")
def vcr_config():
    return {
        "filter_headers": [
            ("authorization", "XXXX"),
            ("x-api-key", "XXXX"),
        ],
    }
```

Run `pytest -m integration` first time to record cassettes. Subsequent runs replay without API calls. Delete cassettes when prompts or tools change.

### Test Cases

| Input | Expected Intent | Key Params to Assert |
|-------|----------------|---------------------|
| "Hola" | `GREET` | — |
| "Qué tienen de bebidas?" | `LIST_PRODUCTS` | `category` present |
| "Una barracuda y una coca cola" | `ADD_TO_CART` | `items` is list, length 2 |
| "Sin cebolla la barracuda" | `UPDATE_CART_ITEM` | `product_name`, `notes` present |
| "Quita la malteada" | `REMOVE_FROM_CART` | `product_name` present |
| "Listo, procede" | `PROCEED_TO_CHECKOUT` | — |
| "Calle 19 #29-99, efectivo" | `SUBMIT_DELIVERY_INFO` | `address` or `payment_method` present |
| "Sí, confirma" | `PLACE_ORDER` | — |
| "Hola, dame una barracuda" | `ADD_TO_CART` (NOT `GREET`) | `items` present |
| "Tienen algo sin gluten?" | `SEARCH_PRODUCTS` | `query` present |

### Cost Control

- Use `gpt-4o-mini` (already the planner model)
- Set `max_tokens=256`
- VCR cassettes avoid repeated API calls in CI
- Run integration tests only pre-deploy, not on every save

---

## Phase 3: End-to-End Scenario Tests

**Goal**: Test complete multi-turn conversation flows through the full system.

### Principle: Grade Outputs, Not Paths

Per Anthropic's guide — don't check if the agent followed exact steps. Check if the final result is correct.

### Scenarios

**Scenario 1 — Happy Path (order placement)**
```
"Hola" → "Qué tienen?" → "Una barracuda" → "Listo" → "Calle 19, efectivo, Juan, 3101234567" → "Sí, confirma"
```
Assert: Order created in DB with correct items, delivery info, and total.

**Scenario 2 — Cart Modifications**
```
"Una barracuda y una coca cola" → "Sin cebolla la barracuda" → "Quita la coca cola" → "Ver carrito"
```
Assert: Cart has 1 item (barracuda), with notes "sin cebolla", coca cola removed.

**Scenario 3 — Returning Customer**
```
Customer with saved delivery info in DB → "Una barracuda" → "Listo" → "Sí, confirma"
```
Assert: Skips delivery info collection, transitions directly to `READY_TO_PLACE`.

**Scenario 4 — Session Timeout**
```
Session older than 120 minutes → new message
```
Assert: State resets to `GREETING`, old order_context cleared.

**Scenario 5 — Edge Cases**
- Empty cart → `PROCEED_TO_CHECKOUT` → should not transition
- Invalid product name → `ADD_TO_CART` → should handle gracefully
- Duplicate item addition → should update quantity, not add duplicate

### Grading (Layered)

Per Anthropic's Swiss Cheese model — no single check catches everything:

- **Code-based grader**: Order in DB? Correct items/quantities? State reset after placement?
- **State verification**: Is `order_context` in the expected state after each turn?
- **Partial credit**: Score multi-step scenarios per component (e.g., 3/4 steps correct is better than 0/4)

---

## Phase 4: Evals (Capability & Regression)

**Goal**: Systematically measure agent quality and prevent regressions over time.

### LangChain Tools Used

```bash
pip install agentevals
```

### Trajectory Evaluation

Use `agentevals` to verify the agent calls the right tools in the right order:

```python
from agentevals.trajectory import create_trajectory_match_evaluator

# Agent must call at least these tools (extras are OK)
evaluator = create_trajectory_match_evaluator(trajectory_match_mode="superset")
```

| Mode | When to use |
|------|------------|
| `strict` | Exact tool call sequence must match |
| `unordered` | Same tools, any order |
| `subset` | Agent calls only tools from reference (no extras) |
| `superset` | Agent calls at least reference tools (extras OK) — **recommended default** |

### Regression Evals (should stay ~100%)

Graduate passing tests from Phases 2 & 3 into a regression suite. Run on every:
- Prompt change (planner or response generator system prompts)
- Model change (e.g., `gpt-4o-mini` → `gpt-4o`)
- Tool modification (new tools, changed signatures)

### Capability Evals (expect low pass rate initially)

These test the frontier — what the agent can't reliably do yet:

| Scenario | Why it's hard |
|----------|--------------|
| "dame lo mismo de siempre" | Requires order history lookup (not implemented) |
| "una barracuda sin cebolla y la dirección es calle 19" | Multi-intent in single message |
| "no espera, cambia la coca cola por una limonada" | Mid-flow correction |
| "parce mándame dos combos al barrio" | Heavy Colombian slang |
| "quiero 3 barracudas, no, 2, bueno sí 3" | Self-correction in same message |

When a capability eval starts passing consistently, **graduate it to regression**.

### LLM-as-Judge (Optional)

For subjective response quality:

```python
from agentevals.trajectory import create_trajectory_llm_as_judge

judge = create_trajectory_llm_as_judge(
    prompt=TRAJECTORY_ACCURACY_PROMPT
)
```

Use sparingly — costs API calls. Good for periodic quality audits, not CI.

### Metrics

Per Anthropic's guide, track both:

- **pass@k** — At least 1 success in k attempts. Use during development.
- **pass^k** — All k attempts succeed. Use for production readiness. If agent has 73% success per task, pass^5 = 20%.

---

## Dependencies

```
pytest
pytest-asyncio
pytest-recording   # VCR for HTTP replay
agentevals         # LangChain trajectory evaluation
```

---

## Implementation Order

| Week | Phase | What |
|------|-------|------|
| 1 | 1a + 1b + 1c | pytest setup, state machine tests, tool tests, parsing tests |
| 2 | 1d + 2 | Pipeline tests with GenericFakeChatModel, planner integration tests |
| 3 | 3 | End-to-end scenario tests |
| Ongoing | 4 | Build eval dataset from real Biela failures, graduate passing tests |

---

## Key Principles (from Anthropic Evals Guide)

1. **Start from real failures** — First test cases should come from actual Biela bugs, not hypotheticals
2. **Grade outputs, not paths** — Don't check exact tool call sequence; check if the result is correct
3. **Partial credit** — Score multi-step scenarios per component
4. **Test both directions** — Test when agent should act AND when it should ask for clarification
5. **Read transcripts** — When tests fail, check if it's the agent or the grader that's wrong
6. **Watch for saturation** — 100% pass rate means your eval is too easy; add harder cases
7. **Layer defenses** — Automated evals + manual review + production monitoring + user feedback
