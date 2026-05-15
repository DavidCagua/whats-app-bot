"""
Tag generator: LLM-assisted Spanish search tag generation for products.

Given a product (name + description + category + business context),
produces 3-6 search tags that cover:
  - the generic category term a customer would say ("cerveza" for POKER)
  - regional/slang equivalents ("salchipapas", "chela")
  - ingredient shortcuts when the name is opaque
  - dietary / preparation markers when relevant

Morphological variants are NOT generated (the search pipeline stems on
query time, so "hervido" is enough to match "hervidito").

Used by scripts/generate_product_metadata.py for bulk tagging. Idempotent:
skips products that already have tags unless forced.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


TAG_GENERATOR_SYSTEM = """Eres un experto en búsqueda de productos para restaurantes colombianos.
Tu tarea: generar entre 3 y 6 etiquetas de búsqueda en español para un producto del menú.

Reglas obligatorias:
1. Las etiquetas deben ser términos que un cliente real usaría al buscar el producto,
   especialmente términos genéricos que NO aparecen en el nombre.
   Ejemplo: si el producto es "POKER" (una cerveza), etiquetas útiles son:
     cerveza, beer, rubia, chela, pola
   NO: poker, marca, lager (muy técnico).

2. Incluye sinónimos regionales colombianos y términos de jerga cuando apliquen.
   Ejemplos: "gaseosa" / "refresco" / "soda"; "pola" / "chela" para cerveza;
   "salchipapas" para papas con salchicha; "perro" para hot dog.

3. Si el nombre del producto es transparente (ej. "Limonada de fresa"),
   incluye términos generales como "limonada", "jugo", "bebida fría" y el sabor.

4. NO repitas el nombre exacto del producto (ya está indexado).

5. NO incluyas: marcas, precios, SKU, nombres propios de dueños, palabras vacías
   ("el", "la", "una"), variantes morfológicas (plural, diminutivo — el motor
   de búsqueda ya las maneja).

6. Etiquetas en minúsculas, sin acentos, una o dos palabras cada una.

7. Devuelve EXCLUSIVAMENTE un JSON array de strings. Nada más.
"""

TAG_GENERATOR_USER_TEMPLATE = """Producto: {name}
Categoría: {category}
Descripción: {description}
Contexto del negocio: {business_context}

Genera 3-6 etiquetas de búsqueda en JSON array:"""


def _get_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except Exception as e:
        logger.error("[TAG_GEN] Failed to init OpenAI client: %s", e)
        return None


def _parse_tags_json(text: str) -> List[str]:
    """Best-effort extraction of a JSON array of strings from an LLM response."""
    if not text:
        return []
    s = text.strip()
    # Strip markdown fences
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        # Try to pluck the first [...] block
        m = re.search(r"\[.*\]", s, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    cleaned: List[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        tag = item.strip().lower()
        if not tag or len(tag) > 40:
            continue
        cleaned.append(tag)
    # De-dupe while preserving order
    seen = set()
    out: List[str] = []
    for t in cleaned:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:6]


def generate_tags_for_product(
    name: str,
    description: Optional[str],
    category: Optional[str],
    business_context: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> List[str]:
    """
    Generate tags for a single product via LLM.

    Returns a (possibly empty) list of tags. Safe to call without an API key
    — returns [] silently.
    """
    client = _get_client()
    if client is None:
        return []

    user_msg = TAG_GENERATOR_USER_TEMPLATE.format(
        name=name or "(sin nombre)",
        category=category or "(sin categoría)",
        description=(description or "(sin descripción)")[:500],
        business_context=business_context or "restaurante de comida colombiana",
    )
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": TAG_GENERATOR_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        content = response.choices[0].message.content or ""
        return _parse_tags_json(content)
    except Exception as e:
        logger.warning("[TAG_GEN] Failed for %s: %s", name, e)
        return []


def build_embedding_text(product: Dict[str, Any]) -> str:
    """
    Build the text blob used to compute a product embedding.

    Includes name, category, description, and tags — everything a customer
    might mention. Keeps the embedding semantically rich without bloating.
    """
    parts: List[str] = []
    if product.get("name"):
        parts.append(str(product["name"]))
    if product.get("category"):
        parts.append(f"categoría: {product['category']}")
    if product.get("description"):
        parts.append(str(product["description"]))
    tags = product.get("tags") or []
    if tags:
        parts.append("etiquetas: " + ", ".join(tags))
    return ". ".join(parts)
