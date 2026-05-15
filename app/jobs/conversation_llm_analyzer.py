"""Pass 2: short LLM summary on every daily conversation.

Runs for every bucket — including ``automatic_completed`` — because even
a successful order can hide inconsistencies worth surfacing (wrong item
typed, customer had to correct the bot, ambiguous address). The summary
narrates what happened; ``has_issues`` is a separate boolean so the
Slack post can count "completed-with-issues" alongside "completed".

Output is a JSON object:

  {
    "summary":         "1-2 oraciones en español: qué pasó",
    "has_issues":      bool,   # true if the bot mishandled anything
    "drop_off_reason": str|null  # only meaningful for automatic_dropped_off
  }

Returns None on any failure so the caller can leave the SQL classification
intact and move on. Token cost is bounded by ``MAX_TRANSCRIPT_TURNS``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


MODEL = "gpt-4o-mini"
# Last N message turns kept in the transcript. 40 covers the long tail
# (median day-conversation is <15) and caps prompt size at ~3k tokens.
MAX_TRANSCRIPT_TURNS = 40
MAX_OUTPUT_TOKENS = 250

_SYSTEM_PROMPT = """Eres un analista que revisa conversaciones de WhatsApp \
de un restaurante colombiano (Biela). Recibes el transcript completo de un \
día con un solo cliente y una categoría asignada por reglas determinísticas. \
Tu trabajo es resumir qué pasó Y señalar cualquier inconsistencia o fricción, \
incluso si el pedido se completó correctamente.

Devuelve SIEMPRE un objeto JSON con exactamente estas tres llaves:

{
  "summary": "1-2 oraciones en español describiendo qué pasó. Si hubo inconsistencias o fricción, MENCIÓNALAS aquí explícitamente, incluso si el pedido se cerró bien.",
  "has_issues": true | false,
  "drop_off_reason": "1 oración sobre en qué punto se cayó la conversación, o null si no aplica"
}

Qué cuenta como inconsistencia / issue (marca has_issues=true):
- El bot entendió mal un producto, precio, cantidad o promoción.
- El cliente tuvo que repetir información (dirección, método de pago, etc.).
- El bot pidió datos que el cliente ya había dado.
- El bot dio información incorrecta o contradictoria.
- El cliente se quejó o mostró frustración.
- Hubo confusión sobre la dirección de entrega.
- El bot tardó mucho en confirmar algo simple.
- Cualquier comportamiento que un humano consideraría "raro" o "mal manejado".

Qué NO es un issue:
- Que el cliente haga muchas preguntas (es normal).
- Que el cliente cambie de opinión (es normal).
- Conversaciones cortas o que solo piden precios.

Reglas adicionales:
- ``summary`` siempre en español, máximo 40 palabras, sin emojis.
- ``has_issues`` debe ser true incluso si la conversación terminó con un pedido exitoso, si hubo fricción significativa en el camino.
- ``drop_off_reason`` SOLO cuando la categoría sea ``automatic_dropped_off``; en los demás casos devuelve null.
- No inventes información que no esté en el transcript.
- No menciones el nombre del negocio en el summary.
"""


def _fetch_transcript(
    *,
    session: Session,
    business_id: str,
    whatsapp_id: str,
    analysis_date: date,
) -> list[dict]:
    """Pull the day's messages in chronological order."""
    rows = session.execute(
        text(
            """
            SELECT role, agent_type, message, timestamp
              FROM conversations
             WHERE business_id = :business_id
               AND whatsapp_id = :whatsapp_id
               AND (timestamp AT TIME ZONE 'America/Bogota')::date = :analysis_date
             ORDER BY timestamp ASC
            """
        ),
        {
            "business_id": uuid.UUID(business_id),
            "whatsapp_id": whatsapp_id,
            "analysis_date": analysis_date,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


def _format_transcript(messages: list[dict]) -> str:
    """Tail of the day's messages, labelled by role for the LLM."""
    tail = messages[-MAX_TRANSCRIPT_TURNS:]
    lines: list[str] = []
    for m in tail:
        if m["role"] == "user":
            who = "CLIENTE"
        elif m["agent_type"] is None:
            # assistant + null agent_type = staff sent directly from admin
            who = "STAFF"
        else:
            who = "BOT"
        msg = (m["message"] or "").strip().replace("\n", " ")
        # Trim individual messages so one giant paste doesn't dominate.
        if len(msg) > 400:
            msg = msg[:400] + "…"
        lines.append(f"{who}: {msg}")
    return "\n".join(lines)


def analyze_conversation(
    *,
    session: Session,
    business_id: str,
    whatsapp_id: str,
    analysis_date: date,
    category: str,
) -> Optional[dict]:
    """Returns ``{summary, has_issues, drop_off_reason, model}`` or None on failure."""
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("[LLM_ANALYZER] OPENAI_API_KEY not set — skipping")
        return None

    messages = _fetch_transcript(
        session=session,
        business_id=business_id,
        whatsapp_id=whatsapp_id,
        analysis_date=analysis_date,
    )
    if not messages:
        return None

    transcript = _format_transcript(messages)
    user_prompt = (
        f"Categoría asignada: {category}\n\n"
        f"Transcript del día (orden cronológico):\n{transcript}"
    )

    try:
        from openai import OpenAI
        client = OpenAI()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0,
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        summary = (parsed.get("summary") or "").strip() or None
        has_issues = bool(parsed.get("has_issues"))
        drop_off_reason = parsed.get("drop_off_reason")
        if isinstance(drop_off_reason, str):
            drop_off_reason = drop_off_reason.strip() or None
        else:
            drop_off_reason = None
        # Hard rule: drop_off_reason is only meaningful for dropped_off bucket.
        if category != "automatic_dropped_off":
            drop_off_reason = None
        return {
            "summary": summary,
            "has_issues": has_issues,
            "drop_off_reason": drop_off_reason,
            "model": MODEL,
        }
    except json.JSONDecodeError as exc:
        logger.error("[LLM_ANALYZER] response not valid JSON: %s", exc)
        return None
    except Exception as exc:
        logger.error("[LLM_ANALYZER] call failed: %s", exc, exc_info=True)
        return None
