#!/usr/bin/env python3
"""
Example script demonstrating calendar tools usage.
This script shows how to use the calendar tools directly.
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

load_dotenv()

def example_calendar_operations():
    """Demonstrate calendar operations."""
    try:
        from services.calendar_tools import (
            list_calendar_events,
            create_calendar_event,
            update_calendar_event,
            delete_calendar_event
        )

        print("📅 Calendar Tools Example")
        print("=" * 40)
        print()

        # Example 1: List current events
        print("1. Listing current events...")
        events = list_calendar_events(max_results=5)
        print(f"   Found {len(events)} events")
        for event in events:
            if isinstance(event, dict) and 'summary' in event:
                print(f"   - {event['summary']} ({event['start']})")
        print()

        # Example 2: Create a test event
        print("2. Creating a test event...")
        now = datetime.utcnow()
        start_time = (now + timedelta(hours=1)).isoformat() + 'Z'
        end_time = (now + timedelta(hours=2)).isoformat() + 'Z'

        result = create_calendar_event(
            summary="Example Event",
            start_time=start_time,
            end_time=end_time,
            description="This is an example event created by the WhatsApp bot",
            location="Example Location"
        )

        if result and 'success' in result:
            event_id = result.get('event', {}).get('id')
            print(f"   ✅ Event created successfully!")
            print(f"   Event ID: {event_id}")
            print(f"   Event: {result.get('event', {}).get('summary')}")
        else:
            print(f"   ❌ Failed to create event: {result}")
            return
        print()

        # Example 3: Update the event
        print("3. Updating the event...")
        new_start = (now + timedelta(hours=1, minutes=30)).isoformat() + 'Z'
        new_end = (now + timedelta(hours=2, minutes=30)).isoformat() + 'Z'

        update_result = update_calendar_event(
            event_id=event_id,
            summary="Updated Example Event",
            start_time=new_start,
            end_time=new_end,
            description="This event has been updated"
        )

        if update_result and 'success' in update_result:
            print(f"   ✅ Event updated successfully!")
            print(f"   New summary: {update_result.get('event', {}).get('summary')}")
        else:
            print(f"   ❌ Failed to update event: {update_result}")
        print()

        # Example 4: List events again to see the changes
        print("4. Listing events after updates...")
        updated_events = list_calendar_events(max_results=5)
        print(f"   Found {len(updated_events)} events")
        for event in updated_events:
            if isinstance(event, dict) and 'summary' in event:
                print(f"   - {event['summary']} ({event['start']})")
        print()

        # Example 5: Delete the test event
        print("5. Deleting the test event...")
        delete_result = delete_calendar_event(event_id)

        if delete_result and 'success' in delete_result:
            print(f"   ✅ Event deleted successfully!")
        else:
            print(f"   ❌ Failed to delete event: {delete_result}")
        print()

        print("🎉 All examples completed successfully!")

    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you have installed all dependencies:")
        print("pip install -r requirements.txt")
    except Exception as e:
        print(f"❌ Example failed: {e}")

def example_langchain_integration():
    """Demonstrate LangChain integration with calendar tools."""
    try:
        from services.langchain_service import langchain_service

        print("🤖 LangChain Integration Example")
        print("=" * 40)
        print()

        # Example 1: Simple conversation
        print("1. Simple conversation...")
        response = langchain_service.generate_response(
            "Hello, how are you?",
            "example_wa_id",
            "Example User"
        )
        print(f"   Response: {response[:100]}...")
        print()

        # Example 2: Calendar-related request
        print("2. Calendar request...")
        response = langchain_service.generate_response(
            "Show me my upcoming events",
            "example_wa_id",
            "Example User"
        )
        print(f"   Response: {response[:200]}...")
        print()

        # Example 3: Create event request
        print("3. Create event request...")
        response = langchain_service.generate_response(
            "Create a meeting tomorrow at 2 PM for 1 hour called 'Team Meeting'",
            "example_wa_id",
            "Example User"
        )
        print(f"   Response: {response[:200]}...")
        print()

        print("✅ LangChain integration examples completed!")

    except Exception as e:
        print(f"❌ LangChain example failed: {e}")

if __name__ == '__main__':
    print("🚀 Calendar Tools Examples")
    print("=" * 50)
    print()

    # Run calendar tools examples
    example_calendar_operations()
    print()

    # Run LangChain integration examples
    example_langchain_integration()
    print()

    print("📚 For more information, see CALENDAR_INTEGRATION.md")