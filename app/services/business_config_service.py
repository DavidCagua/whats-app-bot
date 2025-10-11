"""
Business Configuration Service
Dynamically loads business information from database instead of hardcoded values.
Generic service for any business type (barbershops, salons, restaurants, etc.)
"""

import logging
from typing import Optional, Dict, List, Any

class BusinessConfigService:
    """Service for loading business-specific configuration from database."""

    def get_business_info(self, business_context: Optional[Dict] = None) -> Dict:
        """
        Get complete business information from business context.

        Args:
            business_context: Business context from webhook routing

        Returns:
            Dictionary with business information (name, services, hours, etc.)
        """
        if not business_context:
            logging.warning("[CONFIG] No business context provided, using defaults")
            return self._get_default_config()

        business = business_context.get('business', {})
        settings = business.get('settings', {})

        return {
            'business_name': business.get('name', 'Business'),
            'business_type': business.get('business_type', 'service'),
            'address': settings.get('address', ''),
            'phone': settings.get('phone', ''),
            'city': settings.get('city', ''),
            'state': settings.get('state', ''),
            'country': settings.get('country', 'Colombia'),
            'timezone': settings.get('timezone', 'America/Bogota'),
            'business_hours': settings.get('business_hours', {}),
            'services': settings.get('services', []),
            'payment_methods': settings.get('payment_methods', []),
            'promotions': settings.get('promotions', []),
            'staff': settings.get('staff', []),  # Generic: staff members (barbers, stylists, chefs, etc.)
            'language': settings.get('language', 'es-CO'),
            'ai_personality': settings.get('ai_personality', 'friendly_professional')
        }

    def get_services_list(self, business_context: Optional[Dict] = None) -> List[Dict]:
        """Get list of services/products offered by the business."""
        info = self.get_business_info(business_context)
        return info.get('services', [])

    def get_services_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of services and prices."""
        services = self.get_services_list(business_context)
        if not services:
            return "Servicios disponibles (consultar precios)"

        business_type = self.get_business_info(business_context).get('business_type', 'service')
        icon = self._get_business_icon(business_type)

        text = f"{icon} **SERVICIOS Y PRECIOS**\n\n"
        for service in services:
            name = service.get('name', 'Servicio')
            price = service.get('price', 0)
            duration = service.get('duration', 0)
            if duration:
                text += f"• {name}: ${price:,} COP ({duration} min)\n"
            else:
                text += f"• {name}: ${price:,} COP\n"
        return text

    def get_hours_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of business hours."""
        info = self.get_business_info(business_context)
        hours = info.get('business_hours', {})

        if not hours:
            return "Horarios de atención (consultar)"

        text = "🕐 **HORARIOS DE ATENCIÓN**\n\n"
        day_names = {
            'monday': 'Lunes',
            'tuesday': 'Martes',
            'wednesday': 'Miércoles',
            'thursday': 'Jueves',
            'friday': 'Viernes',
            'saturday': 'Sábado',
            'sunday': 'Domingo'
        }

        for day_key, day_name in day_names.items():
            day_hours = hours.get(day_key, {})
            if day_hours.get('open') == 'closed':
                text += f"• {day_name}: Cerrado\n"
            else:
                open_time = day_hours.get('open', '')
                close_time = day_hours.get('close', '')
                if open_time and close_time:
                    text += f"• {day_name}: {open_time} - {close_time}\n"

        return text

    def get_payment_methods_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of payment methods."""
        info = self.get_business_info(business_context)
        methods = info.get('payment_methods', [])

        if not methods:
            return "💳 Aceptamos varios métodos de pago"

        text = "💳 **MEDIOS DE PAGO**\n\n"
        for method in methods:
            text += f"• {method}\n"
        return text

    def get_promotions_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of current promotions."""
        info = self.get_business_info(business_context)
        promotions = info.get('promotions', [])

        if not promotions:
            return ""

        text = "🎉 **PROMOCIONES ACTUALES**\n\n"
        for promo in promotions:
            text += f"• {promo}\n"
        return text

    def get_staff_list(self, business_context: Optional[Dict] = None) -> List[Dict]:
        """Get list of staff members (barbers, stylists, chefs, etc.)."""
        info = self.get_business_info(business_context)
        return info.get('staff', [])

    def get_staff_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of staff members."""
        staff = self.get_staff_list(business_context)
        if not staff:
            return ""

        info = self.get_business_info(business_context)
        business_type = info.get('business_type', 'service')
        staff_title = self._get_staff_title(business_type)

        text = f"👥 **{staff_title.upper()}**\n\n"
        for member in staff:
            name = member.get('name', '')
            specialties = member.get('specialties', [])
            if specialties:
                text += f"• {name}: {', '.join(specialties)}\n"
            else:
                text += f"• {name}\n"
        return text

    def get_location_info(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted location information."""
        info = self.get_business_info(business_context)

        address = info.get('address', '')
        city = info.get('city', '')
        state = info.get('state', '')

        if address:
            return f"📍 **UBICACIÓN**\n\n{address}, {city}, {state}"
        return "📍 Ubicación disponible por solicitud"

    def get_setting(self, key: str, business_context: Optional[Dict] = None, default: Any = None) -> Any:
        """Get a specific setting from business configuration."""
        info = self.get_business_info(business_context)
        return info.get(key, default)

    def _get_business_icon(self, business_type: str) -> str:
        """Get emoji icon based on business type."""
        icons = {
            'barberia': '💈',
            'salon': '💇',
            'restaurant': '🍽️',
            'cafe': '☕',
            'spa': '💆',
            'gym': '🏋️',
            'clinic': '🏥',
            'default': '🏪'
        }
        return icons.get(business_type, icons['default'])

    def _get_staff_title(self, business_type: str) -> str:
        """Get appropriate staff title based on business type."""
        titles = {
            'barberia': 'Barberos',
            'salon': 'Estilistas',
            'restaurant': 'Nuestro Equipo',
            'cafe': 'Baristas',
            'spa': 'Terapeutas',
            'gym': 'Entrenadores',
            'clinic': 'Profesionales',
            'default': 'Nuestro Equipo'
        }
        return titles.get(business_type, titles['default'])

    def _get_default_config(self) -> Dict:
        """Fallback configuration when no business context available."""
        return {
            'business_name': 'Business',
            'business_type': 'service',
            'address': '',
            'phone': '',
            'city': '',
            'state': '',
            'country': 'Colombia',
            'timezone': 'America/Bogota',
            'business_hours': {},
            'services': [],
            'payment_methods': ['Efectivo', 'Tarjeta'],
            'promotions': [],
            'staff': [],
            'language': 'es-CO',
            'ai_personality': 'friendly_professional'
        }


# Global instance
business_config_service = BusinessConfigService()
