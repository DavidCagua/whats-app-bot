"""
Business Configuration Service
Dynamically loads business information from database instead of hardcoded values.
Generic service for any business type (barbershops, salons, restaurants, etc.)
"""

import logging
from typing import Optional, Dict, List, Any
from app.services.staff_service import staff_service
from app.database.models import Service, get_db_session
import uuid

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
            'payment_methods': settings.get('payment_methods', []),
            'promotions': settings.get('promotions', []),
            'staff': settings.get('staff', []),  # Generic: staff members (barbers, stylists, chefs, etc.)
            'language': settings.get('language', 'es-CO'),
            'ai_personality': settings.get('ai_personality', 'friendly_professional')
        }

    def get_services_list(self, business_context: Optional[Dict] = None) -> List[Dict]:
        """Get list of active services offered by the business from services table."""
        if not business_context:
            return []

        business_id = business_context.get('business_id')
        if not business_id:
            return []

        try:
            session = get_db_session()
            rows = session.query(Service).filter(
                Service.business_id == uuid.UUID(business_id),
                Service.is_active.is_(True)
            ).order_by(Service.name.asc()).all()
            result = [row.to_dict() for row in rows]
            session.close()
            return result
        except Exception as e:
            logging.error(f"[CONFIG] Error loading services for business {business_id}: {e}")
            return []

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
        """Get list of staff members (barbers, stylists, chefs, etc.) from the staff_members table."""
        if not business_context:
            return []
        
        business_id = business_context.get('business_id')
        if not business_id:
            return []
        
        # Use staff_service to get staff from database
        staff_list = staff_service.get_staff_by_business(business_id, active_only=True)
        return [
            {
                'name': s['name'],
                'role': s['role'],
                'specialties': [s['role']]  # Map role to specialties for compatibility
            }
            for s in staff_list
        ]

    def get_staff_text(self, business_context: Optional[Dict] = None) -> str:
        """Get formatted text of staff members from the staff_members table."""
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
            'payment_methods': ['Efectivo', 'Tarjeta'],
            'promotions': [],
            'staff': [],
            'language': 'es-CO',
            'ai_personality': 'friendly_professional'
        }


# Global instance
business_config_service = BusinessConfigService()
