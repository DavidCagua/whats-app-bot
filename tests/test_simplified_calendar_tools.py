#!/usr/bin/env python3
"""
Test suite for simplified calendar tools architecture.
Tests the new schedule_appointment, reschedule_appointment, and cancel_appointment tools.
"""

import os
import sys
import pytest
from datetime import datetime, timedelta

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.calendar_tools import (
    schedule_appointment,
    reschedule_appointment,
    cancel_appointment,
    get_available_slots
)

# Test WhatsApp ID for isolation
TEST_WHATSAPP_ID = "test_user_simplified_001"

def test_schedule_appointment_basic():
    """Test basic appointment scheduling functionality."""
    print("\n=== Testing schedule_appointment ===")

    # Calculate tomorrow's date for testing
    tomorrow = datetime.now() + timedelta(days=1)
    start_time = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)

    # Format as ISO string
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%S")

    # Schedule appointment
    result = schedule_appointment.invoke({
        "whatsapp_id": TEST_WHATSAPP_ID,
        "summary": "Corte y barba - Test",
        "start_time": start_iso,
        "end_time": end_iso,
        "description": "Test appointment for simplified architecture"
    })

    print(f"Schedule result: {result}")
    assert "agendada exitosamente" in result
    assert "parce" in result
    return result

def test_reschedule_appointment():
    """Test appointment rescheduling functionality."""
    print("\n=== Testing reschedule_appointment ===")

    # First, ensure we have an appointment to reschedule
    test_schedule_appointment_basic()

    # Calculate new time (tomorrow afternoon)
    tomorrow = datetime.now() + timedelta(days=1)
    new_start_time = tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
    new_end_time = new_start_time + timedelta(hours=1)

    # Format as ISO string
    new_start_iso = new_start_time.strftime("%Y-%m-%dT%H:%M:%S")
    new_end_iso = new_end_time.strftime("%Y-%m-%dT%H:%M:%S")

    # Reschedule appointment
    result = reschedule_appointment.invoke({
        "whatsapp_id": TEST_WHATSAPP_ID,
        "new_start_time": new_start_iso,
        "new_end_time": new_end_iso,
        "appointment_selector": "latest"
    })

    print(f"Reschedule result: {result}")
    assert ("reagendada exitosamente" in result) or ("No se encontraron citas" in result)
    return result

def test_cancel_appointment():
    """Test appointment cancellation functionality."""
    print("\n=== Testing cancel_appointment ===")

    # First, ensure we have an appointment to cancel
    test_schedule_appointment_basic()

    # Cancel the appointment
    result = cancel_appointment.invoke({
        "whatsapp_id": TEST_WHATSAPP_ID,
        "appointment_selector": "latest"
    })

    print(f"Cancel result: {result}")
    assert ("cancelada exitosamente" in result) or ("No se encontraron citas" in result)
    return result

def test_get_available_slots():
    """Test getting available time slots."""
    print("\n=== Testing get_available_slots ===")

    # Test different time ranges
    tomorrow = datetime.now() + timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")

    # Test morning slots
    morning_result = get_available_slots.invoke({
        "date": date_str,
        "time_range": "morning"
    })
    print(f"Morning slots: {morning_result}")
    assert "Horarios disponibles" in morning_result or "no hay horarios disponibles" in morning_result

    # Test afternoon slots
    afternoon_result = get_available_slots.invoke({
        "date": date_str,
        "time_range": "afternoon"
    })
    print(f"Afternoon slots: {afternoon_result}")
    assert "Horarios disponibles" in afternoon_result or "no hay horarios disponibles" in afternoon_result

    return morning_result, afternoon_result

def test_complete_appointment_flow():
    """Test the complete appointment flow: schedule -> reschedule -> cancel."""
    print("\n=== Testing Complete Appointment Flow ===")

    try:
        # 1. Schedule appointment
        print("1. Scheduling appointment...")
        schedule_result = test_schedule_appointment_basic()

        # 2. Reschedule appointment
        print("2. Rescheduling appointment...")
        reschedule_result = test_reschedule_appointment()

        # 3. Cancel appointment
        print("3. Cancelling appointment...")
        cancel_result = test_cancel_appointment()

        print("\n✅ Complete appointment flow test passed!")
        return True

    except Exception as e:
        print(f"\n❌ Complete appointment flow test failed: {e}")
        return False

def run_all_tests():
    """Run all simplified calendar tool tests."""
    print("🧪 Running Simplified Calendar Tools Test Suite")
    print("=" * 50)

    tests = [
        ("Schedule Appointment", test_schedule_appointment_basic),
        ("Available Slots", test_get_available_slots),
        ("Reschedule Appointment", test_reschedule_appointment),
        ("Cancel Appointment", test_cancel_appointment),
        ("Complete Flow", test_complete_appointment_flow)
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
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 50)

    passed = 0
    for test_name, status, details in results:
        print(f"{status} {test_name}")
        if status == "✅ PASS":
            passed += 1

    print(f"\n🎯 Results: {passed}/{len(results)} tests passed")
    return results

if __name__ == "__main__":
    run_all_tests()