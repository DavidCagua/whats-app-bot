"""
Create + submit-for-approval the Twilio Content Template used by the
order-flow "confirm order" step.

Mirrors how the greeting CTA template was set up. After this runs,
take the printed Content SID and store it on each business that should
use the CTA:

    UPDATE businesses
    SET settings = jsonb_set(
        coalesce(settings, '{}'::jsonb),
        '{confirm_order_content_sid}',
        '"HX..."'::jsonb
    )
    WHERE id = '<business_uuid>';

Run:

    python scripts/create_confirm_order_cta_template.py
    python scripts/create_confirm_order_cta_template.py --skip-approval   # template only
    python scripts/create_confirm_order_cta_template.py --reuse           # don't recreate if friendly_name exists

Reads TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN from .env. Same creds for
local + prod (Twilio Content API is account-scoped, not env-scoped),
so the SID this returns is usable everywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


# -- Template definition ---------------------------------------------------

# Friendly name is the dedup key Twilio shows in the console. Bumping
# it (e.g. _v2) is how you ship a new revision; old templates are kept
# for backward compatibility with messages already sent.
FRIENDLY_NAME = "biela_confirm_order_cta"
LANGUAGE = "es"

# {{1}} carries the multi-line recap (name/address/phone/payment/total).
# Keep the body short — WhatsApp truncates long bodies and the buttons
# get pushed below the fold on small screens.
BODY = (
    "Tengo estos datos para tu pedido:\n\n"
    "{{1}}\n\n"
    "¿Confirmamos el pedido?"
)

# Single confirm button. We deliberately don't offer a "Cambiar algo"
# button — if the customer wants to change something they just type it
# ("quita la coca", "otra dirección…"), and the planner routes it
# normally. A "Cambiar" button would only acknowledge and force the bot
# to immediately re-ask what to change, which is friction with no gain.
# Title MUST match the planner's CONFIRMACIÓN vocabulary so the inbound
# `Body` ("Confirmar pedido") classifies as CONFIRM. No emoji prefix.
BUTTONS = [
    {"id": "confirm_order_yes", "title": "Confirmar pedido"},
]

# Sample variable used by Twilio for previews + WhatsApp's review.
SAMPLE_VARIABLES = {
    "1": (
        "*Nombre:* Yisela\n"
        "*Dirección:* Cl 20 #42-105 ap 1102\n"
        "*Teléfono:* 3015349690\n"
        "*Pago:* Transferencia\n"
        "*Total:* $35.000"
    ),
}

# WhatsApp template category. UTILITY = transactional/order updates,
# which is exactly what the confirm step is. MARKETING would force
# 24-hour-window restrictions and gets stricter review.
WHATSAPP_CATEGORY = "UTILITY"
APPROVAL_NAME = "biela_confirm_order"


# -- Twilio API ------------------------------------------------------------

CONTENT_API = "https://content.twilio.com/v1/Content"


def _auth() -> tuple[str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        sys.exit("error: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing in .env")
    return sid, token


def find_existing(friendly_name: str) -> dict | None:
    """Return the latest Content with matching friendly_name, or None."""
    r = requests.get(
        CONTENT_API,
        auth=_auth(),
        params={"PageSize": 100},
        timeout=20,
    )
    r.raise_for_status()
    contents = r.json().get("contents", [])
    matches = [c for c in contents if c.get("friendly_name") == friendly_name]
    if not matches:
        return None
    matches.sort(key=lambda c: c.get("date_created") or "", reverse=True)
    return matches[0]


def create_template() -> dict:
    body = {
        "friendly_name": FRIENDLY_NAME,
        "language": LANGUAGE,
        "variables": SAMPLE_VARIABLES,
        "types": {
            "twilio/quick-reply": {
                "body": BODY,
                "actions": BUTTONS,
            },
        },
    }
    r = requests.post(
        CONTENT_API,
        auth=_auth(),
        headers={"Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=20,
    )
    if not r.ok:
        sys.exit(f"create failed: {r.status_code} {r.text}")
    return r.json()


def submit_for_whatsapp_approval(content_sid: str) -> dict:
    """
    Submit the template for WhatsApp approval. Twilio review is async;
    status will start as 'received' and transition to 'approved' or
    'rejected' over the next few minutes/hours.
    """
    url = f"{CONTENT_API}/{content_sid}/ApprovalRequests/whatsapp"
    body = {"name": APPROVAL_NAME, "category": WHATSAPP_CATEGORY}
    r = requests.post(
        url,
        auth=_auth(),
        headers={"Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=20,
    )
    if not r.ok:
        sys.exit(f"approval submission failed: {r.status_code} {r.text}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--reuse", action="store_true",
        help="Reuse existing template with the same friendly_name instead of creating a new one.",
    )
    ap.add_argument(
        "--skip-approval", action="store_true",
        help="Create the Content but don't submit for WhatsApp review (e.g. for local sandbox testing).",
    )
    args = ap.parse_args()

    if args.reuse:
        existing = find_existing(FRIENDLY_NAME)
        if existing:
            print(f"reusing existing Content: {existing['sid']}  ({existing.get('date_created')})")
            content = existing
        else:
            print(f"no existing Content named {FRIENDLY_NAME!r}; creating new")
            content = create_template()
            print(f"created Content: {content['sid']}")
    else:
        content = create_template()
        print(f"created Content: {content['sid']}")

    sid = content["sid"]
    print(f"  friendly_name: {content.get('friendly_name')}")
    print(f"  language:      {content.get('language')}")
    print(f"  body:          {BODY!r}")
    print(f"  buttons:       {[b['title'] for b in BUTTONS]}")

    if args.skip_approval:
        print("\nskipping WhatsApp approval (--skip-approval). For sandbox-only use, the SID is usable as-is.")
    else:
        print("\nsubmitting for WhatsApp approval…")
        approval = submit_for_whatsapp_approval(sid)
        status = approval.get("status") or approval.get("whatsapp", {}).get("status") or "unknown"
        print(f"  approval status: {status}  (will transition to 'approved' over the next minutes/hours)")
        print("  category:        UTILITY")
        print("  template name:   " + APPROVAL_NAME)

    print(
        "\nNext step: store this SID on every business that should use the CTA. SQL:\n"
        f"\n  UPDATE businesses\n"
        f"  SET settings = jsonb_set(\n"
        f"        coalesce(settings, '{{}}'::jsonb),\n"
        f"        '{{confirm_order_content_sid}}',\n"
        f"        '\"{sid}\"'::jsonb\n"
        f"      )\n"
        f"  WHERE id = '<business_uuid>';\n"
    )


if __name__ == "__main__":
    main()
