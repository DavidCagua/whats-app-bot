"""
Intent Validator for the Booking Agent — FASE 1.
Classifies user messages into booking intents using regex heuristics (no LLM call).

Intents:
  GREET            — greeting / small talk
  ASK_AVAILABILITY — asking when slots are open
  BOOK             — wants to make an appointment
  CONFIRM          — confirming a pending proposal
  CANCEL           — cancelling a pending proposal or appointment
  RESCHEDULE       — rescheduling an existing appointment
  OUT_OF_SCOPE     — anything else
"""

import re
from typing import Literal

Intent = Literal[
    "GREET",
    "ASK_AVAILABILITY",
    "BOOK",
    "CONFIRM",
    "CANCEL",
    "RESCHEDULE",
    "OUT_OF_SCOPE",
]

# Ordered from most-specific to least-specific.
# First matching pattern wins for its intent category.
_PATTERNS: list[tuple[Intent, list[str]]] = [
    ("CONFIRM", [
        r"\bsi\b",
        r"\bsí\b",
        r"\bconfirm",
        r"\bconfirmo\b",
        r"\bperfecto\b",
        r"\bclaro\b",
        r"\blisto\b",
        r"\bva\b",
        r"\bdale\b",
        r"\byes\b",
        r"\bok\b",
        r"\bde acuerdo\b",
        r"\bacepto\b",
        r"\bcorrecto\b",
        r"\bexacto\b",
        r"\bafirmativo\b",
        r"\bva que va\b",
        r"\banótame\b",
    ]),
    ("CANCEL", [
        r"\bcancelar?\b",
        r"\bcancela\b",
        r"\bno quiero\b",
        r"\bno gracias\b",
        r"\bya no\b",
        r"\bolvidar?\b",
        r"\bnah\b",
        r"\bcancel\b",
        r"\bdéjalo?\b",
        r"\bdejalo?\b",
        r"\bno (la )?confirmo\b",
        r"\bno (lo )?confirmo\b",
    ]),
    ("RESCHEDULE", [
        r"\breagend",
        r"\bcambiar? (la )?cita\b",
        r"\bcambiar? (el )?horario\b",
        r"\breschedul",
        r"\bmover? (la )?cita\b",
        r"\bposponer?\b",
        r"\bcambiar? (la )?hora\b",
    ]),
    ("GREET", [
        r"^hola\b",
        r"^buenos?\b",
        r"^buenas?\b",
        r"^hey\b",
        r"^hi\b",
        r"^hello\b",
        r"^qué tal",
        r"^como est",
        r"^saludos",
        r"^buen día",
        r"^buen dia",
    ]),
    ("ASK_AVAILABILITY", [
        r"\bdisponib",
        r"\bhorari",
        r"\bhueco",
        r"\bslot\b",
        r"\bcuándo (hay|tienen|puedo)\b",
        r"\bcuando (hay|tienen|puedo)\b",
        r"\bqué días\b",
        r"\bque dias\b",
        r"\bqué horas\b",
        r"\bque horas\b",
        r"\btienes? (horas|tiempo|espacio|cupo)\b",
        r"\bhay (horas|citas|cupo)\b",
    ]),
    ("BOOK", [
        r"\bquiero (una? )?cita\b",
        r"\bagendar?\b",
        r"\breservar?\b",
        r"\bbook\b",
        r"\bprogramar? (una? )?cita\b",
        r"\bpedir (una? )?cita\b",
        r"\bsacar (una? )?cita\b",
        r"\bnecesito (una? )?cita\b",
        r"\bquisiera (una? )?cita\b",
    ]),
]


def classify_intent(message: str) -> Intent:
    """
    Classify a user message into a booking intent using regex heuristics.

    Args:
        message: Raw user message text.

    Returns:
        Intent string.
    """
    text = (message or "").lower().strip()
    for intent, patterns in _PATTERNS:
        for pat in patterns:
            if re.search(pat, text):
                return intent
    return "OUT_OF_SCOPE"
