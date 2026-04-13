"""
Scenario eval harness for the order agent.

Adopts the LangChain `agentevals` trajectory pattern documented at
https://docs.langchain.com/oss/python/langchain/test/evals — specifically:

- `create_trajectory_match_evaluator(mode="superset")` for deterministic
  "the planner must have called intent X with these params" assertions.
- `create_trajectory_llm_as_judge` with the tuned
  `TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE` for prose-level quality checks.

Where we diverge from the canonical pattern (deliberately):

1. Our OrderAgent is NOT a `create_agent()` LangGraph agent. It's a
   hand-rolled planner → executor → responder pipeline where tool calls
   are Python dispatches, not LLM-emitted tool_calls. We therefore
   SYNTHESIZE a trajectory from the run: we capture the planner's
   parsed intent + params, the executor's result dict, and the final
   response, and assemble a 4-message trajectory that agentevals can
   evaluate. The evaluators don't care who built the trajectory, only
   that it has the expected shape.

2. We ship hermetic service stubs (session, product catalog, conversation,
   booking, order tools) so scenarios don't touch Postgres, Supabase, or
   any real DB. The LangChain docs assume you hit real tools during evals
   because their example tools are cheap stateless functions. Ours touch
   real business state — stubbing is non-negotiable.

3. We also retain a response-text layer (`must_not_contain` /
   `must_contain_any`) as a third assertion tier for prose-level
   guardrails that trajectory match cannot express (e.g. "the response
   must not use the word 'disculpa'"). These sit alongside the trajectory
   evaluators, not instead of them.

Running:
    pytest -m eval                        # all (needs OPENAI_API_KEY)
    pytest -m eval -k pizza               # one scenario
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------

@dataclass
class AgentScenario:
    """One declarative turn that will be executed through the real pipeline."""

    name: str
    """Human-readable identifier shown in pytest output."""

    user_message: str
    """What the customer types in THIS turn."""

    # --- state before the turn -------------------------------------------
    initial_order_context: Dict[str, Any] = field(default_factory=dict)
    """
    Seeded session.order_context. Keys: state, items, total, delivery_info,
    pending_disambiguation. Default {} = GREETING, empty cart.
    """

    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    """Prior turns passed to the planner. Each: {"role": "...", "content": "..."}."""

    # --- catalog stubs ---------------------------------------------------
    stub_search_products: Optional[Callable[[str, str], List[Dict]]] = None
    stub_list_products_with_fallback: Optional[Callable[[str, str], List[Dict]]] = None
    stub_list_products: Optional[Callable[[str, Optional[str]], List[Dict]]] = None
    stub_list_categories: Optional[Callable[[str], List[str]]] = None
    stub_place_order_tool_result: Optional[str] = None

    # --- trajectory-level assertions (agentevals) ------------------------
    reference_trajectory: Optional[List[Any]] = None
    """
    List of langchain_core.messages.BaseMessage. When set, the scenario's
    captured trajectory is compared against this reference using
    `create_trajectory_match_evaluator(mode=trajectory_match_mode)`. Use
    "superset" (the default) to assert "at least these tool calls must
    appear"; the actual run is allowed to contain more.
    """

    trajectory_match_mode: str = "superset"
    """strict | unordered | subset | superset — see agentevals docs."""

    tool_args_match_mode: str = "exact"
    """
    exact | ignore | subset | superset — controls how tool_call args are
    compared. Use "ignore" when the planner has multiple valid arg
    shapes for the same intent (e.g. ADD_TO_CART with single product_name
    vs ADD_TO_CART with items list) — the match then only checks the
    tool NAME and leaves args flexible.
    """

    # --- LLM-as-judge (agentevals) ---------------------------------------
    llm_judge_rubric: Optional[str] = None
    """
    Optional additional rubric appended to TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE.
    When set, a trajectory-LLM-judge pass runs with this extra context.
    Leave None to skip the LLM judge (faster, cheaper).
    """

    # --- response-text assertions (our layer — prose-level guardrails) ---
    must_not_contain: List[str] = field(default_factory=list)
    """Regex patterns (case-insensitive) that MUST NOT appear in the final response text."""

    must_contain_any: List[str] = field(default_factory=list)
    """Regex patterns where at least ONE must match the final response text."""


@dataclass
class ScenarioRun:
    """Everything captured from one end-to-end scenario execution."""

    trajectory: List[Any]              # list[BaseMessage] — synthesized from the run
    response: str                      # final assistant reply text
    planner_intent: str                # what the planner classified
    planner_params: Dict[str, Any]     # what the planner extracted
    exec_result: Dict[str, Any]        # what the executor returned


# ---------------------------------------------------------------------------
# Fake services
# ---------------------------------------------------------------------------

class _FakeSession:
    """Minimal SessionStateService replacement keyed on (wa_id, business_id)."""

    def __init__(self, initial_order_context: Dict[str, Any]):
        self._store: Dict[tuple, Dict[str, Any]] = {}
        self._initial = initial_order_context

    def _key(self, wa_id, business_id):
        return (str(wa_id), str(business_id))

    def load(self, wa_id, business_id, timeout_minutes=None):
        key = self._key(wa_id, business_id)
        if key not in self._store:
            self._store[key] = {
                "active_agents": ["order"],
                "order_context": dict(self._initial),
                "booking_context": {},
                "agent_contexts": {},
                "last_order_id": None,
                "last_booking_id": None,
            }
        return {"session": self._store[key], "is_new": False, "is_expired": False}

    def save(self, wa_id, business_id, state_update):
        key = self._key(wa_id, business_id)
        existing = self._store.get(key, {
            "active_agents": [],
            "order_context": {},
            "booking_context": {},
            "agent_contexts": {},
            "last_order_id": None,
            "last_booking_id": None,
        })
        if "active_agents" in state_update:
            existing["active_agents"] = state_update["active_agents"]
        if "order_context" in state_update:
            val = state_update["order_context"]
            if val is None:
                existing["order_context"] = {}
            elif isinstance(val, dict):
                existing["order_context"] = {**existing.get("order_context", {}), **val}
        if "last_order_id" in state_update:
            existing["last_order_id"] = state_update["last_order_id"]
        self._store[key] = existing


class _FakeProductService:
    """Stand-in for product_order_service that returns whatever the scenario declared."""

    def __init__(self, scenario: AgentScenario):
        self.scenario = scenario

    def search_products(self, business_id, query):
        if self.scenario.stub_search_products is None:
            return []
        return list(self.scenario.stub_search_products(business_id, query) or [])

    def list_products_with_fallback(self, business_id, category):
        if self.scenario.stub_list_products_with_fallback is None:
            return []
        return list(self.scenario.stub_list_products_with_fallback(business_id, category) or [])

    def list_products(self, business_id, category=None):
        if self.scenario.stub_list_products is None:
            return []
        return list(self.scenario.stub_list_products(business_id, category) or [])

    def list_categories(self, business_id):
        if self.scenario.stub_list_categories is None:
            return []
        return list(self.scenario.stub_list_categories(business_id) or [])

    def get_product(self, product_id=None, product_name=None, business_id=None):
        # Route name-based lookup through the scenario's search stub so
        # scenarios that declare stub_search_products also cover the
        # add_to_cart → get_product(product_name=...) path without
        # having to stub each method individually.
        if product_name and self.scenario.stub_search_products is not None:
            results = list(self.scenario.stub_search_products(business_id, product_name) or [])
            if len(results) == 1:
                return results[0]
            # Ambiguous or empty — let the caller handle it.
            return results[0] if results else None
        return None


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

BIELA_BUSINESS_ID = "44488756-473b-46d2-a907-9f579e98ecfd"
BIELA_WA_ID = "+573001234567"

BIELA_BUSINESS_CONTEXT = {
    "business_id": BIELA_BUSINESS_ID,
    "business": {
        "id": BIELA_BUSINESS_ID,
        "name": "Biela",
        "settings": {
            "address": "Calle X",
            "phone": "+573000000000",
            "city": "Cali",
            "menu_url": "https://gixlink.com/Biela",
        },
    },
}


def _build_trajectory(
    user_message: str,
    planner_intent: str,
    planner_params: Dict[str, Any],
    exec_result: Dict[str, Any],
    response: str,
) -> List[Any]:
    """
    Synthesize a LangChain-shaped trajectory from a hand-rolled pipeline run.

    The planner's JSON intent + params are projected into an AIMessage
    with a single synthetic tool_call. The executor's result dict becomes
    a ToolMessage. The final response is the closing AIMessage. Total: 4
    messages, the same shape a create_agent() one-tool-call run would
    produce.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    tool_call_id = "planner-1"
    # Serialize exec_result compactly — agentevals only needs a string here.
    # Strip non-serializable keys defensively.
    exec_summary = {
        "success": exec_result.get("success"),
        "result_kind": exec_result.get("result_kind"),
        "state_after": exec_result.get("state_after"),
        "error_kind": exec_result.get("error_kind"),
    }
    # Include order_placed / products if present — useful for judge context.
    for k in ("order_placed", "products", "cart_change", "delivery_status"):
        if k in exec_result and exec_result[k]:
            exec_summary[k] = exec_result[k]
    return [
        HumanMessage(content=user_message),
        AIMessage(
            content="",
            tool_calls=[{
                "id": tool_call_id,
                "name": planner_intent or "UNKNOWN",
                "args": dict(planner_params or {}),
            }],
        ),
        ToolMessage(
            content=json.dumps(exec_summary, default=str, ensure_ascii=False),
            tool_call_id=tool_call_id,
        ),
        AIMessage(content=response or ""),
    ]


def run_scenario(scenario: AgentScenario) -> ScenarioRun:
    """
    Execute the scenario through the real OrderAgent pipeline and return
    everything captured: the final text, the synthesized trajectory, and
    the planner/executor intermediate state. Requires OPENAI_API_KEY.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — eval harness requires real LLM calls")

    from app.agents.order_agent import OrderAgent
    from app.orchestration.order_flow import execute_order_intent as _real_execute

    fake_session = _FakeSession(scenario.initial_order_context)
    fake_product = _FakeProductService(scenario)

    # Capture the planner's classification and the executor's result.
    # Wrapping execute_order_intent is the cleanest observation point:
    # it's called from OrderAgent.execute() exactly once per turn, and it
    # sees the parsed intent + params and returns the full exec_result.
    captured = {
        "intent": None,
        "params": None,
        "exec_result": None,
    }

    def _capturing_execute(*args, **kwargs):
        # Accept both positional and keyword invocation for safety.
        intent = kwargs.get("intent")
        params = kwargs.get("params") or {}
        result = _real_execute(*args, **kwargs)
        captured["intent"] = intent
        captured["params"] = dict(params)
        captured["exec_result"] = dict(result)
        return result

    # place_order stub (only engaged when the scenario provides a canned result).
    def _maybe_stub_place_order(tool_name: str):
        if tool_name == "place_order" and scenario.stub_place_order_tool_result is not None:
            stubbed = MagicMock()
            stubbed.name = "place_order"
            stubbed.invoke = MagicMock(return_value=scenario.stub_place_order_tool_result)
            return stubbed
        # Manual find that does NOT go through the patched _find_tool symbol.
        from app.services import order_tools as _ot
        for t in _ot.order_tools:
            if t.name == tool_name:
                return t
        return None

    captured_reply = {"text": None}

    def _capture_store(wa_id, text, role, business_id=None):
        if role == "assistant":
            captured_reply["text"] = text

    def _fake_cart_for_logging(wa_id, business_id):
        sess = fake_session.load(wa_id, business_id)["session"]
        oc = sess.get("order_context") or {}
        return {
            "items": list(oc.get("items") or []),
            "total": int(oc.get("total") or 0),
        }

    patches = [
        patch("app.database.session_state_service.session_state_service", fake_session),
        patch("app.agents.order_agent.session_state_service", fake_session, create=True),
        patch("app.orchestration.order_flow.session_state_service", fake_session),
        patch("app.orchestration.order_flow.product_order_service", fake_product),
        patch("app.database.product_order_service.product_order_service", fake_product),
        # order_tools imports both singletons into its own module namespace;
        # without these patches the @tool-decorated add_to_cart / search_products
        # bypass the fakes and hit the real DB.
        patch("app.services.order_tools.product_order_service", fake_product),
        patch("app.services.order_tools.session_state_service", fake_session),
        patch("app.agents.order_agent.conversation_service.store_conversation_message",
              side_effect=_capture_store),
        patch("app.agents.order_agent.booking_service.get_availability", return_value=[]),
        patch("app.orchestration.order_flow.order_tools._cart_from_session",
              side_effect=_fake_cart_for_logging),
        patch("app.orchestration.order_flow._get_cart_for_logging",
              side_effect=_fake_cart_for_logging),
        patch("app.orchestration.order_flow._find_tool", side_effect=_maybe_stub_place_order),
        patch("app.orchestration.order_flow._clear_pending_disambiguation"),
        patch("app.agents.order_agent.tracer"),
        # Wrap the executor to capture the planner's decision.
        patch("app.agents.order_agent.execute_order_intent", side_effect=_capturing_execute),
    ]

    for p in patches:
        p.start()
    try:
        agent = OrderAgent()
        loaded = fake_session.load(BIELA_WA_ID, BIELA_BUSINESS_ID)["session"]
        agent.execute(
            message_body=scenario.user_message,
            wa_id=BIELA_WA_ID,
            name="Cliente",
            business_context=BIELA_BUSINESS_CONTEXT,
            conversation_history=list(scenario.conversation_history),
            message_id="eval-turn-1",
            session=loaded,
        )
    finally:
        for p in reversed(patches):
            p.stop()

    if captured_reply["text"] is None:
        raise RuntimeError(
            f"Scenario '{scenario.name}' produced no assistant reply. "
            "Check that the agent pipeline reached conversation_service.store_conversation_message."
        )
    if captured["intent"] is None:
        raise RuntimeError(
            f"Scenario '{scenario.name}' did not reach the executor. "
            "Check that OrderAgent.execute() called execute_order_intent."
        )

    trajectory = _build_trajectory(
        user_message=scenario.user_message,
        planner_intent=captured["intent"],
        planner_params=captured["params"],
        exec_result=captured["exec_result"],
        response=captured_reply["text"],
    )
    return ScenarioRun(
        trajectory=trajectory,
        response=captured_reply["text"],
        planner_intent=captured["intent"],
        planner_params=captured["params"],
        exec_result=captured["exec_result"],
    )


# ---------------------------------------------------------------------------
# Assertions — agentevals + response-text layer
# ---------------------------------------------------------------------------

def assert_scenario(scenario: AgentScenario, run: ScenarioRun) -> None:
    """
    Apply three layers of assertions:

    1. Trajectory match (agentevals, deterministic) — if the scenario
       declares a reference_trajectory.
    2. LLM-as-judge (agentevals, qualitative) — if the scenario declares
       an llm_judge_rubric. Always needs a reference_trajectory.
    3. Response-text regex (our layer, prose guardrails) — always
       applied when patterns are declared.

    A scenario can use any combination. The first two come from the
    canonical LangChain docs pattern; the third is ours for prose rules
    the trajectory pattern can't express.
    """
    errors: List[str] = []

    # --- Layer 1: trajectory match ---------------------------------------
    if scenario.reference_trajectory is not None:
        from agentevals.trajectory.match import create_trajectory_match_evaluator
        evaluator = create_trajectory_match_evaluator(
            trajectory_match_mode=scenario.trajectory_match_mode,  # type: ignore[arg-type]
            tool_args_match_mode=scenario.tool_args_match_mode,  # type: ignore[arg-type]
        )
        verdict = evaluator(
            outputs=run.trajectory,
            reference_outputs=scenario.reference_trajectory,
        )
        if not verdict.get("score"):
            errors.append(
                f"TRAJECTORY_MATCH[{scenario.trajectory_match_mode}] failed: "
                f"planner picked {run.planner_intent} {run.planner_params}, "
                f"evaluator={verdict}"
            )

    # --- Layer 2: LLM judge ----------------------------------------------
    # Two flavors depending on whether the scenario provides a reference:
    #   - With reference → TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE
    #     (the judge grades the actual trajectory vs the expected one).
    #   - Without reference → TRAJECTORY_ACCURACY_PROMPT
    #     (the judge grades internal consistency and task quality alone).
    # The second form is the right choice when we care about prose
    # behavior but the planner routing is phrasing-sensitive — forcing a
    # reference would make the judge fight the trajectory match.
    if scenario.llm_judge_rubric:
        from agentevals.trajectory.llm import (
            create_trajectory_llm_as_judge,
            TRAJECTORY_ACCURACY_PROMPT,
            TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE,
        )
        if scenario.reference_trajectory is not None:
            base_prompt = TRAJECTORY_ACCURACY_PROMPT_WITH_REFERENCE
        else:
            base_prompt = TRAJECTORY_ACCURACY_PROMPT
        combined_prompt = (
            base_prompt
            + "\n\n<additional_rubric>\n"
            + scenario.llm_judge_rubric.strip()
            + "\n</additional_rubric>\n"
        )
        judge = create_trajectory_llm_as_judge(
            model="openai:gpt-4o-mini",
            prompt=combined_prompt,
        )
        judge_kwargs: Dict[str, Any] = {"outputs": run.trajectory}
        if scenario.reference_trajectory is not None:
            judge_kwargs["reference_outputs"] = scenario.reference_trajectory
        verdict = judge(**judge_kwargs)
        if not verdict.get("score"):
            errors.append(
                f"LLM_JUDGE failed: {verdict.get('comment') or verdict}"
            )

    # --- Layer 3: response-text regex ------------------------------------
    for pattern in scenario.must_not_contain:
        if re.search(pattern, run.response, flags=re.IGNORECASE):
            errors.append(f"FORBIDDEN pattern matched: /{pattern}/ in response")

    if scenario.must_contain_any:
        if not any(re.search(p, run.response, flags=re.IGNORECASE) for p in scenario.must_contain_any):
            errors.append(
                "REQUIRED: at least one of these patterns must match but none did:\n  - "
                + "\n  - ".join(f"/{p}/" for p in scenario.must_contain_any)
            )

    if errors:
        raise AssertionError(
            f"Scenario '{scenario.name}' failed:\n\n"
            f"  User said: {scenario.user_message!r}\n"
            f"  Planner picked: {run.planner_intent} {run.planner_params}\n"
            f"  Assistant said: {run.response!r}\n\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Helpers for scenario authors
# ---------------------------------------------------------------------------

def product(name: str, price: float, *, category: str = "BURGERS",
            description: str = "", tags: Optional[List[str]] = None,
            matched_by: Optional[str] = None) -> Dict[str, Any]:
    """Build a minimal product dict for stubbing catalog calls."""
    return {
        "id": f"prod-{name.lower().replace(' ', '-')}",
        "business_id": BIELA_BUSINESS_ID,
        "name": name,
        "description": description,
        "price": float(price),
        "currency": "COP",
        "category": category,
        "sku": None,
        "is_active": True,
        "tags": list(tags or []),
        "metadata": {},
        "matched_by": matched_by,
    }


def expected_planner_call(
    user_message: str,
    intent: str,
    params: Optional[Dict[str, Any]] = None,
    response_placeholder: str = "...",
) -> List[Any]:
    """
    Build a reference trajectory asserting "the planner must have called
    this intent with these params". The actual run is allowed to have
    different message content and any executor result — only the tool
    call name and args are checked in superset mode.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    tool_call_id = "planner-1"
    return [
        HumanMessage(content=user_message),
        AIMessage(
            content="",
            tool_calls=[{
                "id": tool_call_id,
                "name": intent,
                "args": dict(params or {}),
            }],
        ),
        ToolMessage(content="", tool_call_id=tool_call_id),
        AIMessage(content=response_placeholder),
    ]
