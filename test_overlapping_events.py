#!/usr/bin/env python3
"""
Test script to verify overlapping events restriction.
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

def test_overlapping_events():
    """Test the overlapping events restriction."""
    try:
        from services.calendar_tools import check_overlapping_events, create_calendar_event
        from services.langchain_service import LangChainService

        print("🧪 Testing Overlapping Events Restriction...")
        print("=" * 50)
        print()

        # Test 1: Check overlapping events function
        print("1. Testing check_overlapping_events function...")
        start_time = "2025-08-08T10:00:00"
        end_time = "2025-08-08T11:00:00"

        has_overlap, count, message = check_overlapping_events(start_time, end_time)
        print(f"   Start time: {start_time}")
        print(f"   End time: {end_time}")
        print(f"   Has overlap: {has_overlap}")
        print(f"   Event count: {count}")
        print(f"   Message: {message}")
        print()

        # Test 2: Test appointment creation with restriction
        print("2. Testing appointment creation with restriction...")

        # Create a test conversation
        test_conversation = [
            "Hola, quiero agendar una cita",
            "Para mañana a las 10 AM",
            "Un corte y barba para David Caguazango"
        ]

        langchain_service = LangChainService()

        for i, message in enumerate(test_conversation, 1):
            print(f"   Message {i}: {message}")
            response = langchain_service.generate_response(message, "test_overlap_001", "David Caguazango")
            print(f"   Response: {response[:200]}...")
            print()

        print("✅ Overlapping events test completed!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_capacity_limit():
    """Test the capacity limit by trying to create multiple events at the same time."""
    try:
        from services.calendar_tools import create_calendar_event

        print("🧪 Testing Capacity Limit...")
        print("=" * 40)
        print()

        # Try to create multiple events at the same time
        test_events = [
            {
                "summary": "Test Event 1",
                "start_time": "2025-08-08T14:00:00",
                "end_time": "2025-08-08T15:00:00",
                "description": "First test event"
            },
            {
                "summary": "Test Event 2",
                "start_time": "2025-08-08T14:00:00",
                "end_time": "2025-08-08T15:00:00",
                "description": "Second test event"
            },
            {
                "summary": "Test Event 3",
                "start_time": "2025-08-08T14:00:00",
                "end_time": "2025-08-08T15:00:00",
                "description": "Third test event (should be rejected)"
            }
        ]

        results = []
        for i, event in enumerate(test_events, 1):
            print(f"Creating event {i}: {event['summary']}")
            result = create_calendar_event.invoke(event)
            results.append(result)
            print(f"Result: {result}")
            print()

        # Check results
        successful_creations = sum(1 for r in results if "created successfully" in r)
        rejected_creations = sum(1 for r in results if "No se puede agendar" in r)

        print(f"Successful creations: {successful_creations}")
        print(f"Rejected creations: {rejected_creations}")

        if successful_creations <= 2 and rejected_creations >= 1:
            print("✅ Capacity limit working correctly!")
        else:
            print("❌ Capacity limit not working as expected")

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Overlapping Events Tests")
    print("=" * 50)
    print()

    success1 = test_overlapping_events()
    print()
    success2 = test_capacity_limit()

    if success1 and success2:
        print("🎉 All overlapping events tests completed successfully!")
    else:
        print("❌ Some tests failed.")
