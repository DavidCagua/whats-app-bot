#!/usr/bin/env python3
"""
Test script to verify the get_available_slots tool functionality.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_available_slots_tool():
    """Test the get_available_slots tool directly."""
    try:
        from services.calendar_tools import get_available_slots

        print("🧪 Testing Available Slots Tool...")
        print("=" * 50)
        print()

        # Test different scenarios
        test_scenarios = [
            {"date": None, "time_range": "morning"},
            {"date": "2025-08-08", "time_range": "morning"},
            {"date": "2025-08-08", "time_range": "afternoon"},
            {"date": "2025-08-08", "time_range": "all"},
        ]

        for i, scenario in enumerate(test_scenarios, 1):
            print(f"📅 Test {i}: {scenario}")
            result = get_available_slots.invoke(scenario)
            print(f"Resultado: {result}")
            print()

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_available_slots_integration():
    """Test the get_available_slots tool through LangChain."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing Available Slots Integration...")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Test messages that should trigger get_available_slots
        test_messages = [
            "manito agendame pa manana en la manana a que hora tienes disponible?",
            "a que hora tienes disponibilidad?",
            "¿qué horarios tienes libres para mañana?",
            "necesito una cita, ¿cuándo tienes disponible?"
        ]

        for i, message in enumerate(test_messages, 1):
            print(f"👤 Cliente: {message}")
            response = langchain_service.generate_response(message, f"test_slots_{i}", "David")
            print(f"🤖 Bot: {response}")
            print()

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Available Slots Tests")
    print("=" * 50)
    print()

    success1 = test_available_slots_tool()
    print()
    success2 = test_available_slots_integration()

    if success1 and success2:
        print("🎉 All available slots tests completed successfully!")
    else:
        print("❌ Some tests failed.")
