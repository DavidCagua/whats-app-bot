#!/usr/bin/env python
"""
Generate tags + embeddings for products (CLI).

Usage:
    python scripts/generate_product_metadata.py --business-id <uuid>
    python scripts/generate_product_metadata.py --business-id <uuid> --tags-only
    python scripts/generate_product_metadata.py --business-id <uuid> --embeddings-only
    python scripts/generate_product_metadata.py --business-id <uuid> --force
    python scripts/generate_product_metadata.py --business-id <uuid> --dry-run

Idempotent: by default skips products whose tags / embeddings are already set.
Use --force to regenerate.

Requires OPENAI_API_KEY in the environment.
"""

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

# Configure logging BEFORE importing any app modules — some of them call
# logging.getLogger at import time and could otherwise silence our output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    force=True,  # override any earlier basicConfig from dependencies
)
logger = logging.getLogger("generate_product_metadata")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database.models import Product, get_db_session, Business  # noqa: E402
from app.services.tag_generator import (  # noqa: E402
    generate_tags_for_product,
    build_embedding_text,
)
from app.services.embeddings import embed_text, format_vector_literal  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402


def _business_context(db_session, business_id: str) -> str:
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


def run(
    business_id: str,
    *,
    tags_only: bool = False,
    embeddings_only: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not set — cannot generate tags/embeddings")
        return 1

    db_session = get_db_session()
    try:
        business_ctx = _business_context(db_session, business_id)
        products = (
            db_session.query(Product)
            .filter(
                Product.business_id == uuid.UUID(business_id),
                Product.is_active == True,
            )
            .order_by(Product.category, Product.name)
            .all()
        )
        if not products:
            logger.warning("No active products found for business %s", business_id)
            return 0

        logger.info("Processing %d products for %s", len(products), business_ctx)

        tag_updates = 0
        embed_updates = 0

        for product in products:
            pid = str(product.id)
            existing_tags = list(product.tags or [])
            needs_tags = not embeddings_only and (force or not existing_tags)

            new_tags = existing_tags
            if needs_tags:
                generated = generate_tags_for_product(
                    name=product.name,
                    description=product.description,
                    category=product.category,
                    business_context=business_ctx,
                )
                if generated:
                    new_tags = generated
                    tag_updates += 1
                    logger.info("  tags  %-35s → %s", product.name[:35], generated)
                    if not dry_run:
                        product.tags = new_tags
                        db_session.flush()
                else:
                    logger.warning("  tags  %-35s → (none generated)", product.name[:35])

            if not tags_only:
                has_embedding = False
                try:
                    row = db_session.execute(
                        sql_text("SELECT embedding IS NOT NULL AS has FROM products WHERE id = :id"),
                        {"id": pid},
                    ).first()
                    has_embedding = bool(row and row[0])
                except Exception as e:
                    logger.debug("embedding presence check failed: %s", e)

                if force or not has_embedding:
                    prod_dict = {
                        "name": product.name,
                        "description": product.description,
                        "category": product.category,
                        "tags": new_tags,
                    }
                    text = build_embedding_text(prod_dict)
                    vec = embed_text(text)
                    if vec:
                        embed_updates += 1
                        logger.info("  embed %-35s → dim=%d", product.name[:35], len(vec))
                        if not dry_run:
                            db_session.execute(
                                sql_text(
                                    "UPDATE products SET embedding = CAST(:vec AS vector) WHERE id = :id"
                                ),
                                {"vec": format_vector_literal(vec), "id": pid},
                            )
                    else:
                        logger.warning("  embed %-35s → (failed)", product.name[:35])

        if dry_run:
            logger.info("DRY RUN — no changes committed")
            db_session.rollback()
        else:
            db_session.commit()
            logger.info("Committed: tags=%d embeddings=%d", tag_updates, embed_updates)
        return 0
    except Exception as e:
        logger.exception("Generation failed: %s", e)
        try:
            db_session.rollback()
        except Exception:
            pass
        return 1
    finally:
        db_session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", required=True, help="Business UUID")
    parser.add_argument("--tags-only", action="store_true", help="Only generate tags, skip embeddings")
    parser.add_argument("--embeddings-only", action="store_true", help="Only generate embeddings, skip tags")
    parser.add_argument("--force", action="store_true", help="Regenerate even if data exists")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done, no writes")
    args = parser.parse_args()
    sys.exit(
        run(
            business_id=args.business_id,
            tags_only=args.tags_only,
            embeddings_only=args.embeddings_only,
            force=args.force,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
