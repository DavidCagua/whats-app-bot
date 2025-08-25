#!/usr/bin/env python3
"""
Test script for Barbería Pasto assistant functionality.
This script tests the assistant with various scenarios.
"""

import os
import sys
from dotenv import load_dotenv

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

load_dotenv()

def test_barberia_assistant():
    """Test the barbería assistant with various scenarios."""
    try:
        from services.langchain_service import langchain_service

        print("🧪 Testing Barbería Pasto Assistant...")
        print("=" * 50)
        print()

        # Test scenarios
        test_scenarios = [
            {
                "name": "1. Saludo inicial",
                "message": "Hola, ¿cómo están?",
                "description": "Testing basic greeting"
            },
            {
                "name": "2. Consulta de precios",
                "message": "¿Cuánto vale el corte?",
                "description": "Testing price inquiry"
            },
            {
                "name": "3. Agendar cita",
                "message": "Quiero agendar una cita para mañana a las 3 PM para un corte",
                "description": "Testing appointment booking"
            },
            {
                "name": "4. Consulta de horarios",
                "message": "¿Cuáles son sus horarios?",
                "description": "Testing hours inquiry"
            },
            {
                "name": "5. Pregunta sobre medios de pago",
                "message": "¿Puedo pagar con Nequi?",
                "description": "Testing payment methods"
            },
            {
                "name": "6. Consulta en inglés",
                "message": "Hello, how much does a haircut cost?",
                "description": "Testing multilingual capability"
            },
            {
                "name": "7. Consulta sobre servicios",
                "message": "¿Qué estilos de corte hacen?",
                "description": "Testing services inquiry"
            },
            {
                "name": "8. Ver citas existentes",
                "message": "Muéstrame las citas que tengo agendadas",
                "description": "Testing calendar listing"
            }
        ]

        for scenario in test_scenarios:
            print(f"{scenario['name']}: {scenario['description']}")
            print(f"Mensaje: {scenario['message']}")

            response = langchain_service.generate_response(
                scenario['message'],
                "test_client_001",
                "Cliente Test"
            )

            print(f"Respuesta: {response[:200]}...")
            print("-" * 50)
            print()

        print("✅ All barbería assistant tests completed!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def test_business_info():
    """Test the business information service."""
    try:
        from services.barberia_info import barberia_info

        print("🧪 Testing Business Information...")
        print("=" * 30)
        print()

        print("Precios:")
        print(barberia_info.get_prices_summary())
        print()

        print("Horarios:")
        print(barberia_info.get_hours_summary())
        print()

        print("Medios de Pago:")
        print(barberia_info.get_payment_methods())
        print()

        print("Promociones:")
        print(barberia_info.get_promotions())
        print()

        print("✅ Business information test completed!")
        return True

    except Exception as e:
        print(f"❌ Business info test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Barbería Assistant Tests")
    print("=" * 60)
    print()

    # Test business information
    info_success = test_business_info()
    print()

    # Test assistant responses
    assistant_success = test_barberia_assistant()
    print()

    if info_success and assistant_success:
        print("🎉 All tests passed! Your Barbería Pasto assistant is ready!")
    else:
        print("❌ Some tests failed. Please check the errors above.")
