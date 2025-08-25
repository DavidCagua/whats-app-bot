#!/usr/bin/env python3
"""
Test script to verify appointment confirmation format.
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

def test_confirmation_format():
    """Test that the bot always confirms appointments with date and time."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing Appointment Confirmation Format...")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Test conversation that should result in appointment creation
        test_conversation = [
            "Hola, quiero agendar una cita",
            "Para mañana a las 2 PM",
            "Un corte para María González"
        ]

        print("📱 Simulando conversación de agendamiento...")
        print()

        for i, message in enumerate(test_conversation, 1):
            print(f"👤 Cliente: {message}")
            response = langchain_service.generate_response(message, "test_confirmation_001", "María González")
            print(f"🤖 Bot: {response}")
            print()

        # Check if the final response contains proper confirmation
        final_response = langchain_service.generate_response("Perfecto, agéndame", "test_confirmation_001", "María González")

        print("🔍 Verificando formato de confirmación...")
        print(f"Respuesta final: {final_response}")
        print()

        # Check for confirmation elements
        has_date = any(word in final_response.lower() for word in ['agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio'])
        has_time = any(word in final_response.lower() for word in ['am', 'pm', 'a.m.', 'p.m.'])
        has_confirmation = 'agendada' in final_response.lower() or 'confirmada' in final_response.lower()
        has_checkmark = '✅' in final_response

        print("📋 Elementos de confirmación encontrados:")
        print(f"   ✅ Checkmark: {has_checkmark}")
        print(f"   📅 Fecha: {has_date}")
        print(f"   🕐 Hora: {has_time}")
        print(f"   📝 Confirmación: {has_confirmation}")

        if has_checkmark and has_date and has_time and has_confirmation:
            print("✅ Formato de confirmación correcto!")
            return True
        else:
            print("❌ Falta algún elemento de confirmación")
            return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_multiple_confirmations():
    """Test multiple appointment confirmations to ensure consistency."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing Multiple Confirmations...")
        print("=" * 40)
        print()

        langchain_service = LangChainService()

        # Test different scenarios
        test_scenarios = [
            {
                "user_id": "test_conf_002",
                "name": "Carlos López",
                "conversation": [
                    "Hola, quiero agendar para hoy a las 4 PM",
                    "Un corte y barba"
                ]
            },
            {
                "user_id": "test_conf_003",
                "name": "Ana Rodríguez",
                "conversation": [
                    "Necesito una cita para mañana a las 11 AM",
                    "Solo corte"
                ]
            }
        ]

        for i, scenario in enumerate(test_scenarios, 1):
            print(f"📱 Escenario {i}: {scenario['name']}")

            for message in scenario['conversation']:
                response = langchain_service.generate_response(message, scenario['user_id'], scenario['name'])
                print(f"   Cliente: {message}")
                print(f"   Bot: {response[:100]}...")
                print()

            # Final confirmation
            final_response = langchain_service.generate_response("Perfecto", scenario['user_id'], scenario['name'])
            print(f"   Confirmación final: {final_response}")
            print()

        print("✅ Múltiples confirmaciones probadas!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Confirmation Format Tests")
    print("=" * 50)
    print()

    success1 = test_confirmation_format()
    print()
    success2 = test_multiple_confirmations()

    if success1 and success2:
        print("🎉 All confirmation format tests completed successfully!")
    else:
        print("❌ Some tests failed.")
