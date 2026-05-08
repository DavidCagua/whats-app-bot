"""
Create + submit-for-approval the Twilio Content Template used as the
welcome greeting WHEN THE BUSINESS IS CLOSED.

Mirrors ``biela_welcome_cta_v3`` (the open-state welcome) but adds a
third variable carrying the live "we're closed, opens at X" sentence.
The order-availability gate decides which welcome to send: when
``business_info_service.is_taking_orders_now`` returns
``can_take_orders=False`` AND
``business.settings.welcome_closed_content_sid`` is set, this template
fires; otherwise the plain-text greeting (with the closed sentence
appended) is sent.

After this runs, store the printed Content SID on each business that
should use the closed CTA:

    UPDATE businesses
    SET settings = jsonb_set(
        coalesce(settings, '{}'::jsonb),
        '{welcome_closed_content_sid}',
        '"HX..."'::jsonb
    )
    WHERE id = '<business_uuid>';

Run:

    python scripts/create_welcome_closed_cta_template.py
    python scripts/create_welcome_closed_cta_template.py --skip-approval   # template only
    python scripts/create_welcome_closed_cta_template.py --reuse           # don't recreate

Reads TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN from .env. Same creds for
local + prod (Twilio Content API is account-scoped).
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

FRIENDLY_NAME = "biela_welcome_closed_cta"
LANGUAGE = "es"

# {{1}} = business name (e.g. "Biela")
# {{2}} = customer opener ("Hola Yisela " — trailing space, mirrors
#          the open-state welcome template's slot)
# {{3}} = live closed sentence from
#          business_info_service.format_open_status_sentence,
#          e.g. "Por ahora estamos cerrados. Volvemos a abrir el
#          viernes a las 9:00 AM."
BODY = (
    "{{2}}👋 Bienvenido a {{1}} 🍔🔥\n"
    "\n"
    "{{3}}\n"
    "\n"
    "Mientras tanto puedo contarte del menú o resolverte cualquier duda."
)

# Same URL action as the open-state welcome — customers can browse the
# menu while the shop is closed. The URL is hardcoded in the template
# (Twilio rejects pure variables in action.url), so the value is per-
# business (separate template per business).
ACTIONS = [
    {
        "type": "URL",
        "title": "Ver carta",
        "url": "https://gixlink.com/Biela/menu.html",
    },
]

SAMPLE_VARIABLES = {
    "1": "Biela",
    "2": "Hola David ",
    "3": (
        "Por ahora estamos cerrados. "
        "Volvemos a abrir el viernes a las 9:00 AM."
    ),
}

# UTILITY: the closed greeting is service-related (informing the
# customer about availability), not promotional. Same category as the
# open welcome template.
WHATSAPP_CATEGORY = "UTILITY"
APPROVAL_NAME = "biela_welcome_closed"


# -- Twilio API ------------------------------------------------------------

CONTENT_API = "https://content.twilio.com/v1/Content"


def _auth() -> tuple[str, str]:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        sys.exit("error: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing in .env")
    return sid, token


def find_existing(friendly_name: str) -> dict | None:
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
            "twilio/call-to-action": {
                "body": BODY,
                "actions": ACTIONS,
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
    print(f"  actions:       {[a.get('title') for a in ACTIONS]}")

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
        f"        '{{welcome_closed_content_sid}}',\n"
        f"        '\"{sid}\"'::jsonb\n"
        f"      )\n"
        f"  WHERE id = '<business_uuid>';\n"
    )


if __name__ == "__main__":
    main()
