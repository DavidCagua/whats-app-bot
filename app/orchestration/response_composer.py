"""
Response composer: merge N agent replies into one coherent Spanish message.

Invoked by the dispatcher only when >1 agent produced output in the same
turn. For single-agent turns (>90% of traffic) this module is never
called — no latency overhead.

Design invariants:
- The composer does NOT regenerate facts. Prices, product names,
  booking IDs, order IDs, quantities, dates are carried verbatim from
  each agent's output.
- The composer does NOT re-run business logic. It glues prose.
- Low temperature (0) to minimize stylistic drift.
- On LLM failure: fall back to newline-joined concatenation of the
  agent replies. Never crash the turn.

This keeps each agent responsible for its own domain-correct rendering
and limits the composer's scope to prose smoothing.
"""

import logging
import os
from typing import List, Optional


logger = logging.getLogger(__name__)


_COMPOSER_SYSTEM_PROMPT = """Eres un editor de prosa. Recibes varias respuestas independientes producidas por diferentes agentes del bot de un restaurante durante la misma interacción del cliente.

Tu trabajo: fusionar las respuestas en UN solo mensaje coherente, en español colombiano natural, que el cliente pueda leer sin sentir que fue armado por partes.

Reglas ESTRICTAS:
- NO inventes información. Usa solo los datos en las respuestas.
- NO cambies números (precios, totales, IDs de pedido, reservas, horas, cantidades).
- NO cambies nombres de productos ni marcas.
- NO repitas lo mismo dos veces si los agentes lo dicen cada uno — consolida.
- Mantén el tono breve y directo. 2-5 oraciones máximo.
- Si hay confirmaciones de acciones (cart, pedido), déjalas primero.
- Si hay información pura (horarios, direcciones), puede ir después como complemento.

Responde SOLO el mensaje final, sin markdown, sin explicación.
"""


_llm = None


def _get_llm():
    """Lazy-init a cheap LLM for composition."""
    global _llm
    if _llm is not None:
        return _llm
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=300,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("[COMPOSER] init failed: %s", exc)
    return _llm


def compose(messages: List[str]) -> str:
    """
    Merge multiple agent replies into one coherent Spanish message.

    Fallback order:
      1. LLM compose (preferred).
      2. Newline-joined concatenation on LLM failure.
      3. First non-empty message on total failure.

    Always returns a non-empty string if at least one input is non-empty.
    """
    cleaned = [m.strip() for m in (messages or []) if m and m.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        # Defensive: shouldn't reach compose() with 1 message, but handle it.
        return cleaned[0]

    llm = _get_llm()
    if llm is None:
        return _concat_fallback(cleaned)

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        numbered = "\n\n".join(f"Respuesta {i+1}:\n{m}" for i, m in enumerate(cleaned))
        response = llm.invoke(
            [
                SystemMessage(content=_COMPOSER_SYSTEM_PROMPT),
                HumanMessage(content=numbered),
            ],
            config={"run_name": "response_composer"},
        )
        merged = (response.content if hasattr(response, "content") else str(response)).strip()
        if not merged:
            return _concat_fallback(cleaned)
        return merged
    except Exception as exc:
        logger.warning("[COMPOSER] LLM merge failed, falling back to concat: %s", exc)
        return _concat_fallback(cleaned)


def _concat_fallback(messages: List[str]) -> str:
    """Simple newline-joined concatenation. Safe but less natural."""
    return "\n\n".join(messages)
