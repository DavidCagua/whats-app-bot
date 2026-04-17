"""
Integration test configuration — API key validation and VCR setup.
"""

import os
import pytest


@pytest.fixture(autouse=True)
def check_openai_key():
    """Skip integration tests if OPENAI_API_KEY is not set."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — skipping integration test")


@pytest.fixture(scope="session")
def vcr_config():
    """Filter sensitive headers from VCR cassettes."""
    return {
        "filter_headers": [
            ("authorization", "XXXX"),
            ("x-api-key", "XXXX"),
            ("openai-api-key", "XXXX"),
        ],
    }
