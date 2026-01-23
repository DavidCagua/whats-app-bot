"""
Mock mode utilities for local testing without Meta API access.

When MOCK_MODE=true, the application will:
- Skip signature verification
- Mock WhatsApp API calls (log instead of send)
- Allow testing full webhook → agent → tools flow
"""

import os
import logging
from typing import Optional
from unittest.mock import Mock

logger = logging.getLogger(__name__)


def is_mock_mode() -> bool:
    """Check if mock mode is enabled."""
    return os.getenv("MOCK_MODE", "false").lower() == "true"


def mock_send_message(data: str, business_context: Optional[dict] = None) -> Mock:
    """
    Mock version of send_message that logs instead of sending.
    
    Args:
        data: JSON message payload (as string)
        business_context: Optional business context
        
    Returns:
        Mock response object
    """
    import json
    
    try:
        payload = json.loads(data)
        recipient = payload.get("to", "unknown")
        message_text = payload.get("text", {}).get("body", "")
        
        logger.info("=" * 60)
        logger.info("[MOCK MODE] 📱 WhatsApp Message (NOT SENT)")
        logger.info("=" * 60)
        logger.info(f"To: {recipient}")
        if business_context:
            business_name = business_context.get('business', {}).get('name', 'Unknown')
            logger.info(f"Business: {business_name}")
        logger.info(f"Message: {message_text}")
        logger.info("=" * 60)
        
        # Return a mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "messaging_product": "whatsapp",
            "contacts": [{"input": recipient, "wa_id": recipient}],
            "messages": [{"id": f"wamid.mock_{os.urandom(8).hex()}"}]
        })
        mock_response.headers = {"Content-Type": "application/json"}
        return mock_response
        
    except Exception as e:
        logger.error(f"[MOCK MODE] Error in mock_send_message: {e}")
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = json.dumps({"error": str(e)})
        return mock_response
