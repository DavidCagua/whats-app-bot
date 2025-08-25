#!/usr/bin/env python3
"""
Test script to verify the complete appointment flow with detailed confirmation.
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

def test_complete_appointment_flow():
    """Test the complete appointment flow with detailed confirmation."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing Complete Appointment Flow...")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Simulate the complete conversation flow
        test_conversation = [
            "Hola, quiero agendar una cita",
            "Para mañana en la mañana",
            "Un corte y barba para David",
            "a las 11 parce"
        ]

        print("📱 Simulando flujo completo de agendamiento...")
        print()

        for i, message in enumerate(test_conversation, 1):
            print(f"👤 Cliente: {message}")
            response = langchain_service.generate_response(message, "test_complete_001", "David")
            print(f"🤖 Bot: {response}")
            print()

        # Check if the final response contains proper confirmation elements
        final_response = langchain_service.generate_response("perfecto", "test_complete_001", "David")

        print("🔍 Verificando confirmación final...")
        print(f"Respuesta final: {final_response}")
        print()

        # Check for confirmation elements
        has_checkmark = '✅' in final_response
        has_date = any(word in final_response.lower() for word in ['agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio'])
        has_time = any(word in final_response.lower() for word in ['am', 'pm', 'a.m.', 'p.m.'])
        has_service = any(word in final_response.lower() for word in ['corte', 'barba', 'combo', 'servicio'])
        has_name = 'david' in final_response.lower()
        has_confirmation = 'agendada' in final_response.lower() or 'confirmada' in final_response.lower()
        has_enthusiasm = any(word in final_response.lower() for word in ['renovado', 'gracias', 'elegirnos'])

        print("📋 Elementos de confirmación encontrados:")
        print(f"   ✅ Checkmark: {has_checkmark}")
        print(f"   📅 Fecha: {has_date}")
        print(f"   🕐 Hora: {has_time}")
        print(f"   💇 Servicio: {has_service}")
        print(f"   👤 Nombre: {has_name}")
        print(f"   📝 Confirmación: {has_confirmation}")
        print(f"   🎉 Entusiasmo: {has_enthusiasm}")

        required_elements = [has_checkmark, has_date, has_time, has_confirmation]
        optional_elements = [has_service, has_name, has_enthusiasm]

        if all(required_elements):
            print("✅ Confirmación detallada correcta!")
            return True
        else:
            print("❌ Faltan elementos requeridos en la confirmación")
            return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Complete Appointment Flow Test")
    print("=" * 50)
    print()

    success = test_complete_appointment_flow()

    if success:
        print("🎉 Complete appointment flow test completed successfully!")
    else:
        print("❌ Test failed.")
