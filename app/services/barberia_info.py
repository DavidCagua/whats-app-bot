"""
Barbería Pasto - Information Service
Contains all the business information for the barbería assistant
"""

class BarberiaInfo:
    """Information service for Barbería Pasto"""

    # Business Information
    BUSINESS_NAME = "Barbería Pasto"
    LOCATION = "Pasto, Nariño, Colombia"
    ADDRESS = "Calle 18 #25-30, Centro, Pasto"
    PHONE = "+57 300 123 4567"

    # Operating Hours
    HOURS = {
        "monday": "8:00 AM - 7:00 PM",
        "tuesday": "8:00 AM - 7:00 PM",
        "wednesday": "8:00 AM - 7:00 PM",
        "thursday": "8:00 AM - 7:00 PM",
        "friday": "8:00 AM - 8:00 PM",
        "saturday": "8:00 AM - 6:00 PM",
        "sunday": "9:00 AM - 2:00 PM"
    }

    # Services and Prices (in Colombian Pesos)
    SERVICES = {
        "corte_clasico": {
            "name": "Corte Clásico",
            "price": 15000,
            "duration": "30-45 minutos",
            "description": "Corte tradicional con tijera y máquina"
        },
        "corte_moderno": {
            "name": "Corte Moderno",
            "price": 20000,
            "duration": "45-60 minutos",
            "description": "Corte con técnicas modernas y acabados especiales"
        },
        "barba": {
            "name": "Arreglo de Barba",
            "price": 12000,
            "duration": "20-30 minutos",
            "description": "Arreglo completo de barba con tijera y navaja"
        },
        "combo_corte_barba": {
            "name": "Combo Corte + Barba",
            "price": 25000,
            "duration": "60-75 minutos",
            "description": "Corte completo + arreglo de barba"
        },
        "corte_ninos": {
            "name": "Corte para Niños",
            "price": 10000,
            "duration": "20-30 minutos",
            "description": "Corte especializado para niños hasta 12 años"
        },
        "lavado": {
            "name": "Lavado y Secado",
            "price": 8000,
            "duration": "15-20 minutos",
            "description": "Lavado profesional con productos de calidad"
        }
    }

    # Payment Methods
    PAYMENT_METHODS = [
        "Efectivo",
        "Tarjeta de crédito/débito",
        "Nequi",
        "DaviPlata",
        "Transferencia bancaria"
    ]

    # Special Offers
    PROMOTIONS = {
        "martes_jovenes": {
            "name": "Martes de Jóvenes",
            "description": "20% de descuento en todos los servicios para estudiantes",
            "valid_days": ["tuesday"],
            "discount": 0.20
        },
        "combo_familiar": {
            "name": "Combo Familiar",
            "description": "2 cortes + 1 barba por $40.000",
            "valid_days": ["saturday", "sunday"],
            "price": 40000
        }
    }

    # Frequently Asked Questions
    FAQ = {
        "duracion_corte": {
            "question": "¿Cuánto dura un corte?",
            "answer": "Un corte clásico dura entre 30-45 minutos, y uno moderno entre 45-60 minutos. El tiempo puede variar según el estilo y la complejidad."
        },
        "pago_nequi": {
            "question": "¿Puedo pagar con Nequi?",
            "answer": "¡Por supuesto! Aceptamos Nequi, DaviPlata, efectivo, tarjeta y transferencia bancaria. Todos los métodos de pago están disponibles."
        },
        "estilos_corte": {
            "question": "¿Qué estilos de corte hacen?",
            "answer": "Hacemos todo tipo de cortes: clásicos, modernos, degradados, fades, undercuts, pompadours, y más. Siempre adaptamos el estilo a tu gusto y tipo de cabello."
        },
        "servicio_ninos": {
            "question": "¿Tienen servicio para niños?",
            "answer": "¡Sí! Tenemos servicio especializado para niños hasta 12 años. El corte infantil cuesta $10.000 y dura entre 20-30 minutos."
        },
        "sin_cita": {
            "question": "¿Atienden sin cita?",
            "answer": "Sí, atendemos sin cita, pero te recomendamos agendar para evitar esperas. Los horarios más ocupados son los sábados y después de las 5:00 PM."
        },
        "ubicacion": {
            "question": "¿Dónde están ubicados?",
            "answer": "Estamos en Calle 18 #25-30, Centro, Pasto. Frente al Parque de Nariño, muy fácil de encontrar."
        },
        "horarios": {
            "question": "¿Cuáles son sus horarios?",
            "answer": "Lunes a viernes de 8:00 AM a 7:00 PM, sábados de 8:00 AM a 6:00 PM, y domingos de 9:00 AM a 2:00 PM."
        }
    }

    @classmethod
    def get_service_info(cls, service_key=None):
        """Get service information"""
        if service_key:
            return cls.SERVICES.get(service_key, {})
        return cls.SERVICES

    @classmethod
    def get_prices_summary(cls):
        """Get a summary of all prices"""
        summary = "💈 **PRECIOS BARBERÍA PASTO**\n\n"
        for key, service in cls.SERVICES.items():
            summary += f"• {service['name']}: ${service['price']:,}\n"
        return summary

    @classmethod
    def get_hours_summary(cls):
        """Get operating hours summary"""
        summary = "🕐 **HORARIOS DE ATENCIÓN**\n\n"
        for day, hours in cls.HOURS.items():
            day_name = {
                "monday": "Lunes",
                "tuesday": "Martes",
                "wednesday": "Miércoles",
                "thursday": "Jueves",
                "friday": "Viernes",
                "saturday": "Sábado",
                "sunday": "Domingo"
            }.get(day, day.title())
            summary += f"• {day_name}: {hours}\n"
        return summary

    @classmethod
    def get_payment_methods(cls):
        """Get payment methods"""
        return "💳 **MEDIOS DE PAGO**\n\n" + "\n".join([f"• {method}" for method in cls.PAYMENT_METHODS])

    @classmethod
    def get_promotions(cls):
        """Get current promotions"""
        summary = "🎉 **PROMOCIONES ACTUALES**\n\n"
        for key, promo in cls.PROMOTIONS.items():
            summary += f"• **{promo['name']}**: {promo['description']}\n"
        return summary

    @classmethod
    def get_faq_answer(cls, question_key):
        """Get FAQ answer"""
        return cls.FAQ.get(question_key, {}).get('answer', 'No tengo información sobre eso. ¿Te puedo ayudar con algo más?')

# Global instance
barberia_info = BarberiaInfo()
