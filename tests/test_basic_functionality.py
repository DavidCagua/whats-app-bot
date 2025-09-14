#!/usr/bin/env python3
"""
Basic functionality tests to verify the simplified architecture is working.
These are quick smoke tests to ensure core components are functioning.
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_imports():
    """Test that all core modules can be imported successfully."""
    print("🔄 Testing imports...")

    try:
        # Test calendar service import
        from app.services.calendar_service import calendar_service
        print("✅ Calendar service imported successfully")

        # Test calendar tools import
        from app.services.calendar_tools import calendar_tools
        print(f"✅ Calendar tools imported successfully ({len(calendar_tools)} tools available)")

        # List available tools
        print("📋 Available tools:")
        for tool in calendar_tools:
            print(f"  - {tool.name}")

        # Test LangChain service import
        from app.services.langchain_service import langchain_service
        print("✅ LangChain service imported successfully")

        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_tool_availability():
    """Test that the new simplified tools are available and properly configured."""
    print("\n🔧 Testing tool availability...")

    try:
        from app.services.calendar_tools import (
            schedule_appointment,
            reschedule_appointment,
            cancel_appointment,
            get_available_slots
        )

        tools = [
            ("schedule_appointment", schedule_appointment),
            ("reschedule_appointment", reschedule_appointment),
            ("cancel_appointment", cancel_appointment),
            ("get_available_slots", get_available_slots),
        ]

        for tool_name, tool_func in tools:
            if hasattr(tool_func, 'name') and hasattr(tool_func, 'description'):
                print(f"✅ {tool_name}: properly configured")
            else:
                print(f"❌ {tool_name}: missing tool configuration")
                return False

        return True

    except ImportError as e:
        print(f"❌ Tool import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Tool configuration error: {e}")
        return False

def test_langchain_service_basic():
    """Test basic LangChain service functionality."""
    print("\n🤖 Testing LangChain service...")

    try:
        from app.services.langchain_service import langchain_service

        # Test basic service initialization
        if hasattr(langchain_service, 'llm') and hasattr(langchain_service, 'llm_with_tools'):
            print("✅ LangChain service properly initialized")
        else:
            print("❌ LangChain service missing core components")
            return False

        # Test conversation history methods
        if (hasattr(langchain_service, 'get_conversation_history') and
            hasattr(langchain_service, 'store_conversation_history')):
            print("✅ Conversation history methods available")
        else:
            print("❌ Conversation history methods missing")
            return False

        # Test generate_response method
        if hasattr(langchain_service, 'generate_response'):
            print("✅ Response generation method available")
        else:
            print("❌ Response generation method missing")
            return False

        return True

    except Exception as e:
        print(f"❌ LangChain service test error: {e}")
        return False

def test_environment_variables():
    """Test that required environment variables are set."""
    print("\n🌍 Testing environment variables...")

    required_vars = [
        "OPENAI_API_KEY",
        "ACCESS_TOKEN",
        "PHONE_NUMBER_ID",
        "VERIFY_TOKEN"
    ]

    missing_vars = []

    for var in required_vars:
        if os.getenv(var):
            print(f"✅ {var}: configured")
        else:
            print(f"❌ {var}: missing or empty")
            missing_vars.append(var)

    if missing_vars:
        print(f"⚠️  Warning: {len(missing_vars)} environment variables are missing")
        print("   This may cause runtime errors in production")
        return False

    return True

def run_basic_tests():
    """Run all basic functionality tests."""
    print("⚡ Running Basic Functionality Tests")
    print("=" * 50)

    tests = [
        ("Module Imports", test_imports),
        ("Tool Availability", test_tool_availability),
        ("LangChain Service", test_langchain_service_basic),
        ("Environment Variables", test_environment_variables)
    ]

    results = []
    passed = 0

    for test_name, test_func in tests:
        print(f"\n📋 {test_name}:")
        try:
            success = test_func()
            if success:
                results.append((test_name, "✅ PASS"))
                passed += 1
            else:
                results.append((test_name, "❌ FAIL"))
        except Exception as e:
            print(f"❌ Test error: {e}")
            results.append((test_name, "❌ ERROR"))

    # Summary
    print("\n" + "=" * 50)
    print("📊 BASIC TESTS SUMMARY")
    print("=" * 50)

    for test_name, status in results:
        print(f"{status} {test_name}")

    print(f"\n🎯 Results: {passed}/{len(results)} tests passed")

    if passed == len(results):
        print("🎉 All basic tests passed! The simplified architecture is ready.")
    else:
        print(f"⚠️  {len(results) - passed} basic tests failed. Check configuration.")

    return results

if __name__ == "__main__":
    run_basic_tests()