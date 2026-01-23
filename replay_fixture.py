#!/usr/bin/env python3
"""
Replay webhook fixtures locally for testing.

This script allows you to test the full webhook → agent → tools flow
without needing Meta API access. It sends fixture payloads to the local
webhook endpoint and displays the results.

Usage:
    python replay_fixture.py fixtures/simple_greeting.json
    python replay_fixture.py fixtures/appointment_request.json
    python replay_fixture.py fixtures/check_availability.json
    
    # Replay all fixtures
    python replay_fixture.py fixtures/*.json
    
    # With custom server URL
    SERVER_URL=http://localhost:8000 python replay_fixture.py fixtures/simple_greeting.json
"""

import os
import sys
import json
import requests
import argparse
from pathlib import Path
from typing import List, Dict, Any

# Ensure MOCK_MODE is enabled
os.environ["MOCK_MODE"] = "true"


def load_fixture(fixture_path: str) -> Dict[str, Any]:
    """Load a fixture JSON file."""
    try:
        with open(fixture_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Fixture not found: {fixture_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in fixture {fixture_path}: {e}")
        sys.exit(1)


def send_webhook(server_url: str, payload: Dict[str, Any]) -> requests.Response:
    """Send webhook payload to server."""
    url = f"{server_url}/webhook"
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=mock_signature"  # Will be bypassed in mock mode
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response
    except requests.exceptions.ConnectionError:
        print(f"❌ Could not connect to {url}")
        print("   Make sure the server is running: python run.py")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"❌ Request to {url} timed out")
        sys.exit(1)


def extract_message_info(payload: Dict[str, Any]) -> Dict[str, str]:
    """Extract message information from payload."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        contact = value["contacts"][0]
        
        return {
            "message_id": message.get("id", "unknown"),
            "wa_id": contact.get("wa_id", "unknown"),
            "name": contact.get("profile", {}).get("name", "Unknown"),
            "message": message.get("text", {}).get("body", ""),
            "phone_number_id": value.get("metadata", {}).get("phone_number_id", "unknown")
        }
    except (KeyError, IndexError):
        return {
            "message_id": "unknown",
            "wa_id": "unknown",
            "name": "Unknown",
            "message": "",
            "phone_number_id": "unknown"
        }


def print_summary(fixture_path: str, info: Dict[str, str], response: requests.Response):
    """Print a summary of the replay."""
    print("\n" + "=" * 70)
    print(f"📋 Fixture: {fixture_path}")
    print("=" * 70)
    print(f"Message ID: {info['message_id']}")
    print(f"User: {info['name']} ({info['wa_id']})")
    print(f"Phone Number ID: {info['phone_number_id']}")
    print(f"Message: {info['message']}")
    print(f"\nResponse Status: {response.status_code}")
    
    try:
        response_json = response.json()
        print(f"Response Body: {json.dumps(response_json, indent=2)}")
    except:
        print(f"Response Body: {response.text}")
    
    print("=" * 70)
    print("\n💡 Check the server logs above to see:")
    print("   - Agent execution flow")
    print("   - Tool calls and results")
    print("   - Tracing information")
    print("   - Mock message output (if message was generated)")
    print()


def replay_fixture(fixture_path: str, server_url: str = "http://localhost:8000"):
    """Replay a single fixture."""
    print(f"\n🔄 Replaying fixture: {fixture_path}")
    
    # Load fixture
    payload = load_fixture(fixture_path)
    
    # Extract message info
    info = extract_message_info(payload)
    
    # Send webhook
    print(f"📤 Sending to {server_url}/webhook...")
    response = send_webhook(server_url, payload)
    
    # Print summary
    print_summary(fixture_path, info, response)
    
    return response.status_code == 200


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Replay webhook fixtures locally for testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python replay_fixture.py fixtures/simple_greeting.json
  python replay_fixture.py fixtures/*.json
  SERVER_URL=http://localhost:8000 python replay_fixture.py fixtures/appointment_request.json
        """
    )
    parser.add_argument(
        "fixtures",
        nargs="+",
        help="Path(s) to fixture JSON file(s)"
    )
    parser.add_argument(
        "--server-url",
        default=os.getenv("SERVER_URL", "http://localhost:8000"),
        help="Server URL (default: http://localhost:8000 or SERVER_URL env var)"
    )
    
    args = parser.parse_args()
    
    # Check if MOCK_MODE is set
    if os.getenv("MOCK_MODE", "false").lower() != "true":
        print("⚠️  Warning: MOCK_MODE is not enabled. Setting it now...")
        os.environ["MOCK_MODE"] = "true"
    
    print("=" * 70)
    print("🧪 Webhook Fixture Replay Tool")
    print("=" * 70)
    print(f"Server URL: {args.server_url}")
    print(f"Mock Mode: {os.getenv('MOCK_MODE')}")
    print()
    
    # Expand glob patterns
    fixture_paths = []
    for pattern in args.fixtures:
        if "*" in pattern or "?" in pattern:
            from glob import glob
            fixture_paths.extend(glob(pattern))
        else:
            fixture_paths.append(pattern)
    
    if not fixture_paths:
        print("❌ No fixtures found")
        sys.exit(1)
    
    # Replay each fixture
    results = []
    for fixture_path in fixture_paths:
        if not os.path.exists(fixture_path):
            print(f"⚠️  Skipping non-existent file: {fixture_path}")
            continue
        
        success = replay_fixture(fixture_path, args.server_url)
        results.append((fixture_path, success))
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 Summary")
    print("=" * 70)
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for fixture_path, success in results:
        status = "✅" if success else "❌"
        print(f"{status} {fixture_path}")
    
    print(f"\nResults: {passed}/{total} passed")
    print("=" * 70)
    
    if passed == total:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
