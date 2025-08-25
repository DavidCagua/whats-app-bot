#!/usr/bin/env python3
"""
Test script for calendar tools functionality.
This script tests the calendar tools to ensure they work correctly.
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

load_dotenv()

def test_calendar_tools():
    """Test the calendar tools functionality."""
    try:
        from services.calendar_tools import calendar_tools, list_calendar_events, create_calendar_event

        print("🧪 Testing Calendar Tools...")
        print()

        # Test 1: List events
        print("1. Testing list_calendar_events...")
        events_result = list_calendar_events.invoke({"max_results": 5})
        print(f"   Result: {events_result}")
        print()

        # Test 2: Create a test event
        print("2. Testing create_calendar_event...")
        now = datetime.utcnow()
        start_time = (now + timedelta(hours=1)).isoformat() + 'Z'
        end_time = (now + timedelta(hours=2)).isoformat() + 'Z'

        test_event = create_calendar_event.invoke({
            "summary": "Test Event from WhatsApp Bot",
            "start_time": start_time,
            "end_time": end_time,
            "description": "This is a test event created by the WhatsApp bot",
            "location": "Test Location"
        })

        print(f"   Result: {test_event}")
        print()

        # Test 3: List events again to see the new event
        print("3. Testing list_calendar_events after creating event...")
        events_after = list_calendar_events.invoke({"max_results": 5})
        print(f"   Result: {events_after}")
        print()

        print("✅ All tests completed!")
        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you have installed all required dependencies:")
        print("pip install -r requirements.txt")
        return False
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_langchain_integration():
    """Test the LangChain integration with calendar tools."""
    try:
        from services.langchain_service import langchain_service

        print("🧪 Testing LangChain Integration...")
        print()

        # Test 1: Simple message
        print("1. Testing simple message...")
        response = langchain_service.generate_response(
            "Hello, how are you?",
            "test_wa_id",
            "Test User"
        )
        print(f"   Response: {response[:100]}...")
        print()

        # Test 2: Calendar-related message
        print("2. Testing calendar-related message...")
        response = langchain_service.generate_response(
            "Show me my upcoming events",
            "test_wa_id",
            "Test User"
        )
        print(f"   Response: {response[:200]}...")
        print()

        print("✅ LangChain integration tests completed!")
        return True

    except Exception as e:
        print(f"❌ LangChain test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Calendar Tools Tests")
    print("=" * 50)
    print()

    # Test calendar tools
    calendar_success = test_calendar_tools()
    print()

    # Test LangChain integration
    langchain_success = test_langchain_integration()
    print()

    if calendar_success and langchain_success:
        print("🎉 All tests passed! Your WhatsApp bot is ready to use with calendar functionality.")
    else:
        print("❌ Some tests failed. Please check the errors above and fix them.")