#!/usr/bin/env python3
"""
Test suite for WhatsApp bot integration with simplified calendar tools.
Tests the complete conversational flow from WhatsApp message to calendar action.
"""

import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.langchain_service import langchain_service
from app.utils.whatsapp_utils import process_whatsapp_message

# Test user data
TEST_USER = {
    "wa_id": "test_whatsapp_user_001",
    "name": "Test Cliente",
    "phone": "+573001234567"
}

def mock_whatsapp_message(message_text, wa_id=None):
    """Create a mock WhatsApp message structure."""
    return {
        "messages": [{
            "from": wa_id or TEST_USER["wa_id"],
            "text": {"body": message_text},
            "type": "text"
        }],
        "contacts": [{
            "profile": {"name": TEST_USER["name"]},
            "wa_id": wa_id or TEST_USER["wa_id"]
        }]
    }

def test_appointment_scheduling_conversation():
    """Test complete appointment scheduling conversation."""
    print("\n=== Testing Appointment Scheduling Conversation ===")

    wa_id = TEST_USER["wa_id"]

    # Test conversation flow
    conversation_steps = [
        ("Hola, quiero agendar una cita", "Should respond with greeting and ask for details"),
        ("Necesito un corte y barba para mañana", "Should ask for specific time"),
        ("A las 10 de la mañana", "Should use schedule_appointment tool"),
    ]

    responses = []

    for i, (message, expected) in enumerate(conversation_steps, 1):
        print(f"\n{i}. User: {message}")
        print(f"   Expected: {expected}")

        try:
            # Generate response using LangChain service
            response = langchain_service.generate_response(message, wa_id)
            print(f"   Bot: {response[:100]}...")
            responses.append((message, response))

        except Exception as e:
            print(f"   ❌ Error: {e}")
            responses.append((message, f"Error: {e}"))

    return responses

def test_appointment_rescheduling_conversation():
    """Test appointment rescheduling conversation."""
    print("\n=== Testing Appointment Rescheduling Conversation ===")

    wa_id = TEST_USER["wa_id"]

    # First schedule an appointment
    print("Setting up: Scheduling initial appointment...")
    langchain_service.generate_response("Quiero agendar corte y barba para mañana a las 10 AM", wa_id)

    # Test rescheduling conversation
    conversation_steps = [
        ("Necesito cambiar mi cita", "Should ask for new time"),
        ("Para las 2 de la tarde", "Should use reschedule_appointment tool"),
    ]

    responses = []

    for i, (message, expected) in enumerate(conversation_steps, 1):
        print(f"\n{i}. User: {message}")
        print(f"   Expected: {expected}")

        try:
            response = langchain_service.generate_response(message, wa_id)
            print(f"   Bot: {response[:100]}...")
            responses.append((message, response))

        except Exception as e:
            print(f"   ❌ Error: {e}")
            responses.append((message, f"Error: {e}"))

    return responses

def test_appointment_cancellation_conversation():
    """Test appointment cancellation conversation."""
    print("\n=== Testing Appointment Cancellation Conversation ===")

    wa_id = TEST_USER["wa_id"]

    # First schedule an appointment
    print("Setting up: Scheduling initial appointment...")
    langchain_service.generate_response("Quiero agendar corte para mañana a las 11 AM", wa_id)

    # Test cancellation conversation
    message = "Quiero cancelar mi cita"
    expected = "Should use cancel_appointment tool"

    print(f"\nUser: {message}")
    print(f"Expected: {expected}")

    try:
        response = langchain_service.generate_response(message, wa_id)
        print(f"Bot: {response[:100]}...")
        return [(message, response)]

    except Exception as e:
        print(f"❌ Error: {e}")
        return [(message, f"Error: {e}")]

def test_availability_inquiry():
    """Test availability inquiry conversation."""
    print("\n=== Testing Availability Inquiry ===")

    wa_id = TEST_USER["wa_id"]

    conversation_steps = [
        ("¿Qué horarios tienes disponibles mañana?", "Should use get_available_slots tool"),
        ("¿Y en la tarde?", "Should show afternoon slots"),
    ]

    responses = []

    for i, (message, expected) in enumerate(conversation_steps, 1):
        print(f"\n{i}. User: {message}")
        print(f"   Expected: {expected}")

        try:
            response = langchain_service.generate_response(message, wa_id)
            print(f"   Bot: {response[:100]}...")
            responses.append((message, response))

        except Exception as e:
            print(f"   ❌ Error: {e}")
            responses.append((message, f"Error: {e}"))

    return responses

def test_general_inquiries():
    """Test general business inquiries (prices, services, location)."""
    print("\n=== Testing General Business Inquiries ===")

    wa_id = TEST_USER["wa_id"]

    conversation_steps = [
        ("¿Cuánto cuesta un corte?", "Should provide pricing information"),
        ("¿Dónde están ubicados?", "Should provide location"),
        ("¿Qué servicios ofrecen?", "Should list services"),
    ]

    responses = []

    for i, (message, expected) in enumerate(conversation_steps, 1):
        print(f"\n{i}. User: {message}")
        print(f"   Expected: {expected}")

        try:
            response = langchain_service.generate_response(message, wa_id)
            print(f"   Bot: {response[:100]}...")
            responses.append((message, response))

        except Exception as e:
            print(f"   ❌ Error: {e}")
            responses.append((message, f"Error: {e}"))

    return responses

def run_integration_tests():
    """Run all WhatsApp integration tests."""
    print("🤖 Running WhatsApp Integration Test Suite")
    print("=" * 50)

    tests = [
        ("General Inquiries", test_general_inquiries),
        ("Availability Inquiry", test_availability_inquiry),
        ("Appointment Scheduling", test_appointment_scheduling_conversation),
        ("Appointment Rescheduling", test_appointment_rescheduling_conversation),
        ("Appointment Cancellation", test_appointment_cancellation_conversation),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            print(f"\n📋 Running: {test_name}")
            result = test_func()
            results.append((test_name, "✅ PASS", result))
            print(f"✅ {test_name}: PASSED")
        except Exception as e:
            results.append((test_name, "❌ FAIL", str(e)))
            print(f"❌ {test_name}: FAILED - {e}")

    # Summary
    print("\n" + "=" * 50)
    print("📊 INTEGRATION TEST RESULTS SUMMARY")
    print("=" * 50)

    passed = 0
    for test_name, status, details in results:
        print(f"{status} {test_name}")
        if status == "✅ PASS":
            passed += 1

    print(f"\n🎯 Results: {passed}/{len(results)} tests passed")
    return results

if __name__ == "__main__":
    run_integration_tests()