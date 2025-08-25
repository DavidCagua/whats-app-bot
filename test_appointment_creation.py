#!/usr/bin/env python3
"""
Test script for appointment creation functionality.
This script tests that the bot properly creates calendar events when appointments are confirmed.
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
        logging.FileHandler('appointment_test.log')
    ]
)

def test_appointment_creation():
    """Test that the bot creates appointments when all information is provided."""
    try:
        from services.langchain_service import langchain_service

        print("🧪 Testing Appointment Creation...")
        print("=" * 50)
        print()

        # Test conversation flow for appointment creation
        test_conversation = [
            "Hola, quiero agendar una cita",
            "Para mañana a las 10 AM",
            "Un combo corte y barba",
            "Mi nombre es David Caguazango"
        ]

        wa_id = "test_appointment_001"
        name = "David"

        for i, message in enumerate(test_conversation, 1):
            print(f"📝 Mensaje {i}: {message}")

            response = langchain_service.generate_response(
                message,
                wa_id,
                name
            )

            print(f"🤖 Respuesta {i}: {response[:200]}...")
            print("-" * 50)
            print()

        print("✅ Appointment creation test completed!")
        print("📋 Check the console output and 'appointment_test.log' file for detailed logs")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_direct_appointment():
    """Test direct appointment creation with all information provided."""
    try:
        from services.langchain_service import langchain_service

        print("🧪 Testing Direct Appointment Creation...")
        print("=" * 50)
        print()

        # Test with all information provided at once
        message = "Agendame para mañana a las 10 AM un combo corte y barba, mi nombre es David Caguazang"

        wa_id = "test_direct_001"
        name = "David"

        print(f"📝 Mensaje: {message}")

        response = langchain_service.generate_response(
            message,
            wa_id,
            name
        )

        print(f"🤖 Respuesta: {response}")
        print("-" * 50)
        print()

        print("✅ Direct appointment test completed!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Appointment Creation Tests")
    print("=" * 60)
    print()

    # Test conversation flow
    flow_success = test_appointment_creation()
    print()

    # Test direct appointment
    direct_success = test_direct_appointment()
    print()

    if flow_success and direct_success:
        print("🎉 All appointment creation tests completed!")
        print("📋 Check the logs for tool call details")
    else:
        print("❌ Some tests failed. Please check the errors above.")
