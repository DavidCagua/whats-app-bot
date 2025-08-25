#!/usr/bin/env python3
"""
Demonstration of the capacity limit feature.
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

def demo_capacity_limit():
    """Demonstrate the capacity limit in action."""
    try:
        from services.langchain_service import LangChainService

        print("🎯 Capacity Limit Demonstration")
        print("=" * 50)
        print()

        # Create the service
        langchain_service = LangChainService()

        # Simulate a conversation where someone tries to book when capacity is full
        conversation = [
            "Hola, quiero agendar una cita para mañana a las 2 PM",
            "Un corte para Juan Pérez",
            "Perfecto, agéndame"
        ]

        print("📱 Simulando conversación con cliente...")
        print()

        for i, message in enumerate(conversation, 1):
            print(f"👤 Cliente: {message}")
            response = langchain_service.generate_response(message, "demo_capacity_001", "Juan Pérez")
            print(f"🤖 Bot: {response}")
            print()

        print("✅ Demostración completada!")
        print()
        print("📋 Resumen:")
        print("- El bot verifica la capacidad antes de crear eventos")
        print("- Máximo 2 eventos simultáneos permitidos")
        print("- Si hay 2 eventos en el mismo horario, el bot rechaza la cita")
        print("- El bot ofrece horarios alternativos de manera amigable")

        return True

    except Exception as e:
        print(f"❌ Error en la demostración: {e}")
        return False

if __name__ == '__main__':
    print("🚀 Iniciando Demostración de Límite de Capacidad")
    print("=" * 50)
    print()

    success = demo_capacity_limit()

    if success:
        print("🎉 Demostración exitosa!")
    else:
        print("❌ Demostración falló.")
