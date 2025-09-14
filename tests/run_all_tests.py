#!/usr/bin/env python3
"""
Comprehensive test runner for the simplified WhatsApp bot architecture.
Runs all test suites and provides detailed reporting.
"""

import os
import sys
import time
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import test modules
from test_simplified_calendar_tools import run_all_tests as run_calendar_tests
from test_whatsapp_integration import run_integration_tests

def print_banner():
    """Print test suite banner."""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                    WHATSAPP BOT TEST SUITE                       ║
║                     Simplified Architecture                      ║
╚══════════════════════════════════════════════════════════════════╝
    """
    print(banner)

def print_test_section(title):
    """Print a formatted test section header."""
    print(f"\n{'='*70}")
    print(f"🧪 {title}")
    print('='*70)

def print_summary(all_results):
    """Print comprehensive test summary."""
    print(f"\n{'='*70}")
    print("📊 COMPREHENSIVE TEST SUMMARY")
    print('='*70)

    total_tests = 0
    total_passed = 0

    for suite_name, results in all_results.items():
        print(f"\n📋 {suite_name}:")
        passed = 0
        for test_name, status, _ in results:
            print(f"  {status} {test_name}")
            if status == "✅ PASS":
                passed += 1

        print(f"  🎯 Suite Result: {passed}/{len(results)} passed")
        total_tests += len(results)
        total_passed += passed

    print(f"\n{'='*70}")
    print(f"🏆 OVERALL RESULTS: {total_passed}/{total_tests} tests passed")

    if total_passed == total_tests:
        print("🎉 ALL TESTS PASSED! The simplified architecture is working correctly.")
    else:
        print(f"⚠️  {total_tests - total_passed} tests failed. Review the results above.")

def run_complete_test_suite():
    """Run all test suites and provide comprehensive reporting."""
    print_banner()

    start_time = time.time()
    test_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"🕐 Test started at: {test_timestamp}")
    print(f"🏗️  Testing simplified calendar tool architecture")

    all_results = {}

    try:
        # 1. Calendar Tools Tests
        print_test_section("CALENDAR TOOLS TESTS")
        calendar_results = run_calendar_tests()
        all_results["Calendar Tools"] = calendar_results

        # 2. WhatsApp Integration Tests
        print_test_section("WHATSAPP INTEGRATION TESTS")
        integration_results = run_integration_tests()
        all_results["WhatsApp Integration"] = integration_results

        # 3. Print comprehensive summary
        print_summary(all_results)

    except Exception as e:
        print(f"\n❌ Test suite execution failed: {e}")
        return False

    finally:
        end_time = time.time()
        duration = round(end_time - start_time, 2)
        print(f"\n⏱️  Total test duration: {duration} seconds")

    return all_results

def main():
    """Main test execution function."""
    try:
        results = run_complete_test_suite()

        # Determine exit code based on results
        if results:
            total_tests = sum(len(suite_results) for suite_results in results.values())
            total_passed = sum(
                len([r for r in suite_results if r[1] == "✅ PASS"])
                for suite_results in results.values()
            )

            if total_passed == total_tests:
                print(f"\n🎊 SUCCESS: All tests passed!")
                sys.exit(0)
            else:
                print(f"\n💥 FAILURE: {total_tests - total_passed} tests failed")
                sys.exit(1)
        else:
            print(f"\n💥 FAILURE: Test suite execution failed")
            sys.exit(1)

    except KeyboardInterrupt:
        print(f"\n\n⚠️ Test suite interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 FAILURE: Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()