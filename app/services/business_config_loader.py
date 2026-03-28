"""
Business Config Loader — FASE 5.
Loads per-business booking configuration from local JSON files (MVP).
Provides booking-specific settings injected into the booking agent.

JSON files live at: app/services/business_configs/{business_id}.json
Missing keys fall back to DEFAULTS.
"""

import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "business_configs")

DEFAULTS: Dict = {
    "require_confirmation": True,
    "allowed_intents": [
        "GREET",
        "ASK_AVAILABILITY",
        "BOOK",
        "CONFIRM",
        "CANCEL",
        "RESCHEDULE",
        "OUT_OF_SCOPE",
    ],
    "session_timeout_minutes": 120,
}


class BusinessConfigLoader:
    """Load per-business booking configuration, falling back to DEFAULTS."""

    def load(self, business_id: Optional[str]) -> Dict:
        """Return config dict for business_id. Always returns a complete dict."""
        config = dict(DEFAULTS)
        if not business_id:
            return config
        path = os.path.join(_CONFIG_DIR, f"{business_id}.json")
        try:
            if os.path.exists(path):
                with open(path) as f:
                    overrides = json.load(f)
                config.update(overrides)
                logger.info(f"[CONFIG] Loaded business config for {business_id}")
        except Exception as e:
            logger.warning(f"[CONFIG] Could not load config for {business_id}: {e}")
        return config

    def requires_confirmation(self, business_id: Optional[str]) -> bool:
        return bool(self.load(business_id).get("require_confirmation", True))

    def allowed_intents(self, business_id: Optional[str]) -> List[str]:
        return list(self.load(business_id).get("allowed_intents", DEFAULTS["allowed_intents"]))


business_config_loader = BusinessConfigLoader()
