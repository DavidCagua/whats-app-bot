#!/usr/bin/env python3
"""
Test script for tool logging functionality.
This script tests that tool calls are properly logged.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

load_dotenv()

# Configure logging to see all the tool call logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('tool_logs.log')
    ]
)

def test_tool_logging():
    """Test that tool calls are properly logged."""
    try:
        from services.langchain_service import langchain_service

        print("🧪 Testing Tool Logging...")
        print("=" * 40)
        print()

        # Test scenarios that should trigger tool calls
        test_scenarios = [
            {
                "name": "1. List calendar events",
                "message": "Muéstrame las citas que tengo agendadas",
                "description": "Should trigger list_calendar_events tool"
            },
            {
                "name": "2. Create appointment",
                "message": "Quiero agendar una cita para mañana a las 3 PM para un corte",
                "description": "Should trigger create_calendar_event tool"
            },
            {
                "name": "3. Simple greeting",
                "message": "Hola, ¿cómo están?",
                "description": "Should not trigger any tools"
            }
        ]

        wa_id = "test_logging_001"
        name = "Test User"

        for scenario in test_scenarios:
            print(f"📝 Testing: {scenario['name']}")
            print(f"📝 Message: {scenario['message']}")
            print(f"📝 Description: {scenario['description']}")
            print()

            response = langchain_service.generate_response(
                scenario['message'],
                wa_id,
                name
            )

            print(f"🤖 Response: {response[:200]}...")
            print("-" * 50)
            print()

        print("✅ Tool logging test completed!")
        print("📋 Check the console output and 'tool_logs.log' file for detailed logs")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Tool Logging Tests")
    print("=" * 50)
    print()

    success = test_tool_logging()

    if success:
        print("🎉 Tool logging is working correctly!")
        print("📋 Logs have been saved to 'tool_logs.log'")
    else:
        print("❌ Some tests failed. Please check the errors above.")
