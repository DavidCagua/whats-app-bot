#!/usr/bin/env python3
"""
Run scripts/seed_biela_menu.sql against DATABASE_URL from the repo root .env.

Usage (from repo root):
  python scripts/run_seed_biela_menu.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = Path(__file__).resolve().parent / "seed_biela_menu.sql"


def main() -> int:
    try:
        from dotenv import load_dotenv
        import psycopg2
    except ImportError as e:
        print("Install deps: pip install python-dotenv psycopg2-binary", file=sys.stderr)
        raise SystemExit(1) from e

    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set in .env", file=sys.stderr)
        return 1

    raw = SQL_FILE.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if not re.match(r"^\s*--", ln)]
    body = "\n".join(lines)
    parts = [p.strip() for p in body.split(";") if p.strip()]

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        for i, part in enumerate(parts, 1):
            cur.execute(part + ";")
        print(f"OK: {len(parts)} statement(s) from {SQL_FILE.name}")
    except Exception as e:
        print(f"Error on statement {i}: {e}", file=sys.stderr)
        return 1
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
