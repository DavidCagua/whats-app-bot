"""
Capability evals — stretch tests that push the agent's limits.
Expected to have LOW pass rate initially. As the agent improves,
passing tests graduate to test_regression.py.

These test scenarios drawn from real Biela failures and edge cases
that the planner/agent doesn't reliably handle yet.
"""

# TODO: Add eval infrastructure (agentevals trajectory match, LLM-as-judge).
# Each test should document why it's hard and what passing looks like.

# Planned capability evals:
#
# Case: "dame lo mismo de siempre" — requires order history lookup (not yet implemented)
# Case: "una barracuda sin cebolla y la dirección es calle 19" — multi-intent in single message
# Case: "no espera, cambia la coca cola por una limonada" — mid-flow correction
# Case: "parce mándame dos combos al barrio" — heavy Colombian slang
# Case: "quiero 3 barracudas, no, 2, bueno sí 3" — self-correction within same message
# Case: "lo de siempre pero sin la coca cola" — reference to past order + modification
# Case: User sends voice note transcription with typos/fragments
# Case: User sends image of menu item (multimodal — not supported yet)
