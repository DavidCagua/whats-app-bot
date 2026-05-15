"""
Embeddings service: thin wrapper around OpenAI text-embedding-3-small.

Generates 1536-dim vectors for product search. The API key is optional —
if missing, embed_text returns None and the search pipeline falls back to
lexical + tag matching.

Rate limits and retries are intentionally simple — we call this from
offline scripts (bulk indexing) and from the query path (one call per
user message). No async, no pooling.
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _get_client():
    """Lazy OpenAI client. Returns None if no API key is set."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except Exception as e:
        logger.error("[EMBEDDINGS] Failed to init OpenAI client: %s", e)
        return None


def embed_text(text: str) -> Optional[List[float]]:
    """
    Return a 1536-dim embedding for the given text, or None if unavailable.

    Returns None on missing API key, empty text, or any error — callers
    must handle the None case (typically by falling back to lexical search).
    """
    if not text or not text.strip():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip(),
        )
        return list(response.data[0].embedding)
    except Exception as e:
        logger.warning("[EMBEDDINGS] embed_text failed: %s", e)
        return None


def embed_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """
    Batch version — one API call per chunk of up to 100 texts.
    Returns a list aligned with inputs; None for any that fail or are empty.
    """
    if not texts:
        return []
    client = _get_client()
    if client is None:
        return [None] * len(texts)

    results: List[Optional[List[float]]] = [None] * len(texts)
    valid_indices = [i for i, t in enumerate(texts) if t and t.strip()]
    if not valid_indices:
        return results

    CHUNK = 100
    for start in range(0, len(valid_indices), CHUNK):
        chunk_indices = valid_indices[start:start + CHUNK]
        chunk_texts = [texts[i].strip() for i in chunk_indices]
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=chunk_texts,
            )
            for local_i, global_i in enumerate(chunk_indices):
                results[global_i] = list(response.data[local_i].embedding)
        except Exception as e:
            logger.warning(
                "[EMBEDDINGS] embed_batch chunk %d-%d failed: %s",
                start, start + len(chunk_indices), e,
            )
    return results


def format_vector_literal(vec: List[float]) -> str:
    """Format a Python list as a pgvector literal string: '[0.1,0.2,...]'"""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
