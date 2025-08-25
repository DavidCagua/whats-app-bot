#!/usr/bin/env python3
"""
Test script to simulate appointment confirmation and debug the response issue.
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

def test_appointment_confirmation():
    """Test the appointment confirmation flow."""
    try:
        from services.langchain_service import LangChainService

        print("🧪 Testing Appointment Confirmation Flow...")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Simulate the exact conversation from the user
        test_conversation = [
            "Hola, quiero agendar una cita",
            "Para mañana a las 10:30 AM",
            "Un corte moderno con barba para David",
            "si porfa"  # This should trigger the appointment creation
        ]

        print("📱 Simulando conversación de confirmación...")
        print()

        for i, message in enumerate(test_conversation, 1):
            print(f"👤 Cliente: {message}")
            response = langchain_service.generate_response(message, "test_conf_004", "David")
            print(f"🤖 Bot: {response}")
            print()

        # Test the specific "si porfa" response
        print("🔍 Probando respuesta específica a 'si porfa'...")
        final_response = langchain_service.generate_response("si porfa", "test_conf_004", "David")
        print(f"Respuesta final: '{final_response}'")
        print(f"Longitud: {len(final_response) if final_response else 0}")
        print(f"Está vacía: {not final_response or not final_response.strip()}")

        # Check for confirmation elements
        has_confirmation = 'agendada' in final_response.lower() or 'confirmada' in final_response.lower()
        has_date = any(word in final_response.lower() for word in ['agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio'])
        has_time = any(word in final_response.lower() for word in ['am', 'pm', 'a.m.', 'p.m.'])

        print(f"Contiene confirmación: {has_confirmation}")
        print(f"Contiene fecha: {has_date}")
        print(f"Contiene hora: {has_time}")

        if has_confirmation and has_date and has_time:
            print("✅ Confirmación correcta!")
            return True
        else:
            print("❌ Falta información en la confirmación")
            return False

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Starting Appointment Confirmation Test")
    print("=" * 50)
    print()

    success = test_appointment_confirmation()

    if success:
        print("🎉 Appointment confirmation test completed successfully!")
    else:
        print("❌ Test failed.")
