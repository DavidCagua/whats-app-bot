#!/usr/bin/env python3
"""
Test script to simulate list_calendar_events tool call and debug the response issue.
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

def test_list_events_response():
    """Test the list_calendar_events tool call response."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing List Calendar Events Response...")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Test the exact message that's causing the issue
        test_message = "manito agendame pa manana en la manana a que hora tienes disponible?"

        print(f"👤 Cliente: {test_message}")
        response = langchain_service.generate_response(test_message, "test_list_005", "David")
        print(f"🤖 Bot: {response}")
        print()

        # Check if the response is empty or generic
        is_empty = not response or not response.strip()
        is_generic = "Gracias por tu mensaje" in response or "Te responderé pronto" in response

        print(f"Respuesta vacía: {is_empty}")
        print(f"Respuesta genérica: {is_generic}")
        print(f"Longitud de respuesta: {len(response) if response else 0}")

        if is_empty or is_generic:
            print("❌ Problema detectado: respuesta vacía o genérica")
            return False
        else:
            print("✅ Respuesta correcta generada")
            return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_direct_tool_call():
    """Test the list_calendar_events tool directly."""
    try:
        from services.calendar_tools import list_calendar_events

        print("🧪 Testing Direct Tool Call...")
        print("=" * 40)
        print()

        # Test the tool directly
        result = list_calendar_events.invoke({"max_results": 5})
        print(f"Resultado directo del tool: {result}")
        print()

        if result and "No upcoming events" not in result:
            print("✅ Tool funciona correctamente")
            return True
        else:
            print("❌ Tool no retorna eventos")
            return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting List Events Response Test")
    print("=" * 50)
    print()

    success1 = test_direct_tool_call()
    print()
    success2 = test_list_events_response()

    if success1 and success2:
        print("🎉 All list events tests completed successfully!")
    else:
        print("❌ Some tests failed.")
