#!/usr/bin/env python3
"""
Tests for message deduplication service.
Verifies that duplicate WhatsApp messages are not processed twice.
"""

import os
import sys
import time
from datetime import datetime, timedelta

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.message_deduplication import (
    MessageDeduplicationService,
    LRUCacheWithTTL,
    message_deduplication_service
)
from app.utils.whatsapp_utils import extract_message_id


def create_mock_webhook_payload(message_id: str, message_text: str = "Test message"):
    """Create a mock WhatsApp webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15550555555",
                        "phone_number_id": "123456789"
                    },
                    "contacts": [{
                        "profile": {
                            "name": "Test User"
                        },
                        "wa_id": "573001234567"
                    }],
                    "messages": [{
                        "from": "573001234567",
                        "id": message_id,
                        "timestamp": str(int(time.time())),
                        "text": {
                            "body": message_text
                        },
                        "type": "text"
                    }]
                },
                "field": "messages"
            }]
        }]
    }


def test_extract_message_id():
    """Test message ID extraction from webhook payload."""
    print("\n=== Testing Message ID Extraction ===")
    
    message_id = "wamid.test123456789"
    payload = create_mock_webhook_payload(message_id)
    
    extracted_id = extract_message_id(payload)
    assert extracted_id == message_id, f"Expected {message_id}, got {extracted_id}"
    print(f"✅ Successfully extracted message ID: {extracted_id}")
    
    # Test with missing message ID
    payload_no_id = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{}]
                }
            }]
        }]
    }
    extracted_id = extract_message_id(payload_no_id)
    assert extracted_id is None, f"Expected None for missing ID, got {extracted_id}"
    print("✅ Correctly handled missing message ID")
    
    return True


def test_lru_cache_with_ttl():
    """Test in-memory LRU cache with TTL."""
    print("\n=== Testing LRU Cache with TTL ===")
    
    cache = LRUCacheWithTTL(max_size=3, ttl_seconds=1)  # 1 second TTL for testing
    
    # Test adding and checking
    cache.add("msg1")
    assert cache.has("msg1"), "Message should exist immediately after adding"
    print("✅ Message added and found in cache")
    
    # Test duplicate detection
    assert cache.has("msg1"), "Duplicate should be detected"
    print("✅ Duplicate detection works")
    
    # Test expiration
    cache.add("msg2")
    time.sleep(1.1)  # Wait for TTL to expire
    assert not cache.has("msg2"), "Expired message should not be found"
    print("✅ TTL expiration works correctly")
    
    # Test LRU eviction
    cache.add("msg3")
    cache.add("msg4")
    cache.add("msg5")  # Should evict msg3 (oldest)
    assert not cache.has("msg3"), "Oldest message should be evicted"
    assert cache.has("msg4"), "Recent message should still exist"
    assert cache.has("msg5"), "Most recent message should exist"
    print("✅ LRU eviction works correctly")
    
    return True


def test_deduplication_service():
    """Test message deduplication service."""
    print("\n=== Testing Message Deduplication Service ===")
    
    service = MessageDeduplicationService()
    message_id = "wamid.test_dedup_001"
    
    # Test first message (should not be duplicate)
    is_dup = service.is_duplicate(message_id)
    assert not is_dup, "First message should not be duplicate"
    print("✅ First message correctly identified as new")
    
    # Mark as processed
    service.mark_as_processed(message_id)
    print("✅ Message marked as processed")
    
    # Test duplicate detection
    is_dup = service.is_duplicate(message_id)
    assert is_dup, "Second check should detect duplicate"
    print("✅ Duplicate correctly detected")
    
    # Test different message ID
    different_id = "wamid.test_dedup_002"
    is_dup = service.is_duplicate(different_id)
    assert not is_dup, "Different message should not be duplicate"
    print("✅ Different message correctly identified as new")
    
    return True


def test_webhook_deduplication_flow():
    """Test complete webhook deduplication flow."""
    print("\n=== Testing Webhook Deduplication Flow ===")
    
    message_id = "wamid.test_webhook_001"
    payload1 = create_mock_webhook_payload(message_id, "First message")
    payload2 = create_mock_webhook_payload(message_id, "Duplicate message")
    
    # Extract message IDs
    id1 = extract_message_id(payload1)
    id2 = extract_message_id(payload2)
    
    assert id1 == id2 == message_id, "Message IDs should match"
    print(f"✅ Extracted message ID: {message_id}")
    
    # Simulate first webhook
    service = MessageDeduplicationService()
    is_dup1 = service.is_duplicate(id1)
    assert not is_dup1, "First webhook should not be duplicate"
    service.mark_as_processed(id1)
    print("✅ First webhook processed")
    
    # Simulate duplicate webhook (same message ID)
    is_dup2 = service.is_duplicate(id2)
    assert is_dup2, "Second webhook with same ID should be duplicate"
    print("✅ Duplicate webhook correctly detected")
    
    return True


def test_edge_cases():
    """Test edge cases for deduplication."""
    print("\n=== Testing Edge Cases ===")
    
    service = MessageDeduplicationService()
    
    # Test None/empty message ID
    assert not service.is_duplicate(None), "None should not be duplicate"
    assert not service.is_duplicate(""), "Empty string should not be duplicate"
    print("✅ Handled None/empty message IDs")
    
    # Test very long message ID
    long_id = "wamid." + "x" * 1000
    assert not service.is_duplicate(long_id), "Long ID should work"
    service.mark_as_processed(long_id)
    assert service.is_duplicate(long_id), "Long ID duplicate should be detected"
    print("✅ Handled very long message IDs")
    
    # Test special characters in message ID
    special_id = "wamid.test_123-456.789"
    assert not service.is_duplicate(special_id), "Special chars should work"
    service.mark_as_processed(special_id)
    assert service.is_duplicate(special_id), "Special chars duplicate should work"
    print("✅ Handled special characters in message IDs")
    
    return True


def run_deduplication_tests():
    """Run all deduplication tests."""
    print("🔍 Running Message Deduplication Test Suite")
    print("=" * 50)
    
    tests = [
        ("Message ID Extraction", test_extract_message_id),
        ("LRU Cache with TTL", test_lru_cache_with_ttl),
        ("Deduplication Service", test_deduplication_service),
        ("Webhook Deduplication Flow", test_webhook_deduplication_flow),
        ("Edge Cases", test_edge_cases),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            print(f"\n📋 Running: {test_name}")
            result = test_func()
            results.append((test_name, "✅ PASS", None))
            print(f"✅ {test_name}: PASSED")
        except AssertionError as e:
            results.append((test_name, "❌ FAIL", str(e)))
            print(f"❌ {test_name}: FAILED - {e}")
        except Exception as e:
            results.append((test_name, "❌ ERROR", str(e)))
            print(f"❌ {test_name}: ERROR - {e}")
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 DEDUPLICATION TEST RESULTS SUMMARY")
    print("=" * 50)
    
    passed = 0
    for test_name, status, details in results:
        print(f"{status} {test_name}")
        if details:
            print(f"   {details}")
        if status == "✅ PASS":
            passed += 1
    
    print(f"\n🎯 Results: {passed}/{len(results)} tests passed")
    return results


if __name__ == "__main__":
    run_deduplication_tests()
