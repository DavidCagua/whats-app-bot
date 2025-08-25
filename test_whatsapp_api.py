#!/usr/bin/env python3
"""
Test script to verify WhatsApp API connection and message sending.
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

def test_whatsapp_api():
    """Test the WhatsApp API connection and message sending."""
    try:
        from app.utils.whatsapp_utils import get_text_message_input, send_message

        print("🧪 Testing WhatsApp API Connection...")
        print("=" * 50)
        print()

        # Test phone number (replace with a valid test number)
        # IMPORTANT: This number must be added to the allowed recipients list in your WhatsApp Business API
        test_phone = "573001234567"  # Replace with your actual WhatsApp number
        test_message = "Hola, este es un mensaje de prueba desde el bot de WhatsApp. ✅"

        print(f"📱 Testing with phone: {test_phone}")
        print(f"💬 Test message: {test_message}")
        print()

        # Create the message data
        data = get_text_message_input(test_phone, test_message)
        print(f"📤 Message data: {data}")
        print()

        # Send the message
        print("🚀 Sending message...")
        result = send_message(data)

        if result is not None:
            print("✅ Message sent successfully!")
            print(f"Response status: {result.status_code}")
            print(f"Response body: {result.text}")
        else:
            print("❌ Failed to send message")

        return result is not None

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_configuration():
    """Test if all required environment variables are set."""
    try:
        from app.config import load_configurations
        from flask import Flask

        print("🔧 Testing Configuration...")
        print("=" * 30)
        print()

        app = Flask(__name__)
        load_configurations(app)

        required_configs = [
            "ACCESS_TOKEN",
            "VERSION",
            "PHONE_NUMBER_ID",
            "VERIFY_TOKEN"
        ]

        missing_configs = []
        for config in required_configs:
            value = app.config.get(config)
            if not value:
                missing_configs.append(config)
            else:
                print(f"✅ {config}: {'*' * len(value)} (length: {len(value)})")

        if missing_configs:
            print(f"❌ Missing configurations: {missing_configs}")
            return False
        else:
            print("✅ All required configurations are set!")
            return True

    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting WhatsApp API Tests")
    print("=" * 50)
    print()

    config_success = test_configuration()
    print()

    if config_success:
        api_success = test_whatsapp_api()

        if api_success:
            print("🎉 All WhatsApp API tests completed successfully!")
        else:
            print("❌ WhatsApp API test failed.")
    else:
        print("❌ Configuration test failed. Please check your environment variables.")
