"""
Vision-backed promo extractor for inbound image messages.

Customers screenshot promotions they see on the restaurant's social
media and send them to the bot. This module asks GPT-4o-mini (vision)
to extract just enough structured info that `promotion_service.find_promo_by_query`
can match it against the restaurant's actual promotions table.

The model is NOT asked to decide what to do with the image — only to
extract. The caller (media_job) does the matching + reply.

Cheap (~$0.001 per image), tight scope (one structured extraction call,
JSON response). Returns None on any failure — caller falls back to a
generic "recibí tu imagen" reply.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


_EXTRACTION_PROMPT = """Eres un extractor de información para un bot de WhatsApp de un restaurante.
El cliente envió una imagen. La tarea es decidir si es una captura de pantalla de una
promoción del restaurante (anuncio en redes sociales, poster, flyer) y extraer su contenido.

Devuelve SOLO JSON válido (sin markdown, sin texto extra) con esta forma exacta:
{
  "is_promo_screenshot": true | false,
  "candidate_name": "<nombre claro de la promo, o null si no se identifica>",
  "mentioned_products": ["<producto 1>", "<producto 2>"],
  "promo_text": "<texto legible de la imagen, verbatim>"
}

Reglas:
- is_promo_screenshot = true SOLO si la imagen contiene texto promocional claro
  (precio promo, palabras como "combo", "promoción", "oferta", "2x1", días específicos).
- Una foto de comida sin texto promocional NO es promo (false).
- Un comprobante de pago, captura de chat, foto personal, screenshot de menú genérico
  sin texto promocional → false.
- candidate_name debe ser el TÍTULO de la promo si es legible (ej. "2 Honey Burger con
  papas"). Si solo hay precio sin título claro, ponlo como null.
- mentioned_products: nombres de comida visibles en el texto (puede estar vacío).
"""


def extract_promo_from_image(image_url: str) -> Optional[Dict[str, Any]]:
    """
    Call OpenAI vision (gpt-4o-mini) to classify + extract from an image.

    Returns:
        {
          "is_promo_screenshot": bool,
          "candidate_name": str | None,
          "mentioned_products": list[str],
          "promo_text": str,
        }
        or None when the call can't be made (no API key, no URL, parse
        failure, network error). Caller MUST treat None as "fall back to
        a generic image-receipt reply".
    """
    if not image_url:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("[IMAGE_PROMO] OPENAI_API_KEY not set — skipping extraction")
        return None

    try:
        from openai import OpenAI
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extrae los datos de esta imagen."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=400,
            temperature=0,
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        return {
            "is_promo_screenshot": bool(parsed.get("is_promo_screenshot")),
            "candidate_name": (parsed.get("candidate_name") or "").strip() or None,
            "mentioned_products": [
                str(p).strip() for p in (parsed.get("mentioned_products") or []) if str(p).strip()
            ],
            "promo_text": (parsed.get("promo_text") or "").strip(),
        }
    except json.JSONDecodeError as exc:
        logger.error("[IMAGE_PROMO] response not valid JSON: %s", exc)
        return None
    except Exception as exc:
        logger.error("[IMAGE_PROMO] extraction failed: %s", exc, exc_info=True)
        return None
