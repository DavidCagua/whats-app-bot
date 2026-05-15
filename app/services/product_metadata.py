"""
Per-product tag + embedding regeneration.

Single source of truth for the "compute tags and embedding for one product"
operation. Used by:
  - scripts/generate_product_metadata.py (bulk seed pipeline)
  - app/views.py /admin/products/<id>/regenerate-metadata (live updates from
    the admin console)
"""

import logging
import uuid
from typing import Optional

from sqlalchemy import text as sql_text

from .embeddings import embed_text, format_vector_literal
from .tag_generator import build_embedding_text, generate_tags_for_product
from ..database.models import Business, Product

logger = logging.getLogger(__name__)


def business_context_for(db_session, business_id: str) -> str:
    try:
        business = (
            db_session.query(Business)
            .filter(Business.id == uuid.UUID(business_id))
            .first()
        )
        if not business:
            return "restaurante"
        settings = business.settings or {}
        name = business.name or "restaurante"
        hint = settings.get("business_description") or settings.get("ai_prompt") or ""
        if hint:
            return f"{name} — {hint[:200]}"
        return name
    except Exception:
        return "restaurante"


def regenerate_for_product(
    db_session,
    product: Product,
    *,
    business_context: Optional[str] = None,
    force: bool = False,
    tags_only: bool = False,
    embeddings_only: bool = False,
) -> dict:
    """
    Regenerate tags and/or embedding for a single product.

    Caller is responsible for committing the session. Does NOT commit on its
    own so this composes cleanly inside the bulk script and the Flask handler.

    Returns a small status dict with what was updated.
    """
    pid = str(product.id)
    if business_context is None:
        business_context = business_context_for(db_session, str(product.business_id))

    existing_tags = list(product.tags or [])
    needs_tags = not embeddings_only and (force or not existing_tags)

    new_tags = existing_tags
    tags_updated = False
    if needs_tags:
        generated = generate_tags_for_product(
            name=product.name,
            description=product.description,
            category=product.category,
            business_context=business_context,
        )
        if generated:
            new_tags = generated
            product.tags = new_tags
            db_session.flush()
            tags_updated = True
            logger.info("[METADATA] tags %-35s → %s", (product.name or "")[:35], generated)
        else:
            logger.warning("[METADATA] tags %-35s → (none generated)", (product.name or "")[:35])

    embedding_updated = False
    if not tags_only:
        has_embedding = False
        try:
            row = db_session.execute(
                sql_text("SELECT embedding IS NOT NULL AS has FROM products WHERE id = :id"),
                {"id": pid},
            ).first()
            has_embedding = bool(row and row[0])
        except Exception as e:
            logger.debug("[METADATA] embedding presence check failed: %s", e)

        if force or not has_embedding:
            text = build_embedding_text(
                {
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "tags": new_tags,
                }
            )
            vec = embed_text(text)
            if vec:
                db_session.execute(
                    sql_text(
                        "UPDATE products SET embedding = CAST(:vec AS vector) WHERE id = :id"
                    ),
                    {"vec": format_vector_literal(vec), "id": pid},
                )
                embedding_updated = True
                logger.info("[METADATA] embed %-35s → dim=%d", (product.name or "")[:35], len(vec))
            else:
                logger.warning("[METADATA] embed %-35s → (failed)", (product.name or "")[:35])

    return {
        "product_id": pid,
        "tags_updated": tags_updated,
        "embedding_updated": embedding_updated,
        "tags": new_tags,
    }
