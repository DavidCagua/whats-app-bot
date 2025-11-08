"""
Prompt Builder Service
Generates dynamic AI system prompts from business configuration.
Admin prompts are plain text with no variable injection.
All context (business, customer, runtime) is auto-generated and appended.
"""

import logging
from typing import Optional, Dict
from .business_config_service import business_config_service


class PromptBuilder:
    """Service for building dynamic AI system prompts."""

    def build_system_prompt(
        self,
        business_context: Optional[Dict],
        current_date: str,
        current_year: int,
        wa_id: str,
        name: str
    ) -> str:
        """
        Build complete system prompt from business configuration.

        Args:
            business_context: Business context with settings from database
            current_date: Current date string (DD/MM/YYYY)
            current_year: Current year as integer
            wa_id: WhatsApp ID of customer
            name: Customer name

        Returns:
            Complete system prompt string
        """
        try:
            # Get business information
            business_info = business_config_service.get_business_info(business_context)

            # Get appointment settings
            apt_settings = business_info.get('appointment_settings', {}) if business_context else {}
            if not apt_settings:
                apt_settings = business_context.get('business', {}).get('settings', {}).get('appointment_settings', {}) if business_context else {}
            max_concurrent = apt_settings.get('max_concurrent', 2)

            # Get admin-editable prompt from database (NO variable injection)
            admin_prompt = ""
            if business_context:
                settings = business_context.get('business', {}).get('settings', {})
                admin_prompt = settings.get('ai_prompt', '')

            # If no custom prompt, use a generic one
            if not admin_prompt:
                admin_prompt = self._get_default_prompt()
                logging.warning("[PROMPT] No custom ai_prompt found, using default")

            # Build context section (customer, business, runtime info)
            context_section = self._build_context_section(
                business_info=business_info,
                max_concurrent=max_concurrent,
                name=name,
                wa_id=wa_id,
                current_date=current_date,
                current_year=current_year
            )

            # Build business info section (services, hours, location, etc.)
            business_info_section = self._build_business_info_section(business_context)

            # Assemble final prompt: Admin prompt + Context + Business info
            final_prompt = (
                admin_prompt +
                "\n\n---\n\n" +
                context_section +
                "\n\n---\n\n" +
                business_info_section
            )

            logging.info(f"[PROMPT] Generated dynamic prompt (length: {len(final_prompt)} chars)")
            return final_prompt

        except Exception as e:
            logging.error(f"[PROMPT] Error building system prompt: {e}")
            # Return a safe fallback
            return self._get_fallback_prompt(name, wa_id, current_date, current_year)

    def _build_context_section(
        self,
        business_info: Dict,
        max_concurrent: int,
        name: str,
        wa_id: str,
        current_date: str,
        current_year: int
    ) -> str:
        """
        Build context section with business, customer, and runtime information.
        This replaces the old CORE_TEMPLATE's context variables.
        """
        context = "### CONTEXTO ACTUAL\n\n"

        # Business context
        context += "**Negocio:**\n"
        context += f"- Nombre: {business_info.get('business_name', 'Business')}\n"
        context += f"- Ubicación: {business_info.get('city', '')}, {business_info.get('state', '')}, {business_info.get('country', 'Colombia')}\n"

        phone = business_info.get('phone', '')
        if phone:
            context += f"- Teléfono: {phone}\n"

        context += f"- Zona horaria: {business_info.get('timezone', 'UTC')}\n"
        context += f"- Máximo de citas simultáneas: {max_concurrent}\n"

        context += "\n**Cliente actual:**\n"
        context += f"- Nombre: {name}\n"
        context += f"- WhatsApp ID: {wa_id}\n"

        # Add day of week information
        from datetime import datetime
        try:
            day, month, year = current_date.split('/')
            date_obj = datetime(int(year), int(month), int(day))
            day_names_es = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
            day_of_week = day_names_es[date_obj.weekday()]
        except:
            day_of_week = "desconocido"

        context += "\n**Fecha y hora:**\n"
        context += f"- Fecha actual: {current_date} (DD/MM/YYYY)\n"
        context += f"- Día de la semana: {day_of_week}\n"
        context += f"- Año: {current_year}\n"

        return context

    def _build_business_info_section(self, business_context: Optional[Dict]) -> str:
        """
        Build business information section (services, hours, staff, etc.).
        Auto-generated from database settings.
        """
        sections = []

        # Services and prices
        services = business_config_service.get_services_text(business_context)
        if services:
            sections.append(services)

        # Business hours
        hours = business_config_service.get_hours_text(business_context)
        if hours:
            sections.append(hours)

        # Staff
        staff = business_config_service.get_staff_text(business_context)
        if staff:
            sections.append(staff)

        # Location
        location = business_config_service.get_location_info(business_context)
        if location:
            sections.append(location)

        # Payment methods
        payment = business_config_service.get_payment_methods_text(business_context)
        if payment:
            sections.append(payment)

        # Promotions
        promotions = business_config_service.get_promotions_text(business_context)
        if promotions:
            sections.append(promotions)

        return "\n\n".join(sections)

    def _get_default_prompt(self) -> str:
        """Default prompt when none is configured (no variables)."""
        return """Eres un asistente virtual amigable para el negocio.

Tu función es ayudar a los clientes con:
- Información sobre servicios y precios
- Agendar citas
- Responder preguntas frecuentes

Usa un tono profesional y amigable.

REGLAS IMPORTANTES:
- Verifica disponibilidad antes de confirmar citas
- Siempre confirma con fecha, hora exacta, servicio y nombre del cliente
- Recolecta información del cliente de forma natural: nombre, edad, servicio deseado
- Formato de confirmación: "✅ Tu cita está agendada para el [fecha] a las [hora] para [servicio], [nombre]"
"""

    def _get_fallback_prompt(self, name: str, wa_id: str, current_date: str, current_year: int) -> str:
        """Emergency fallback prompt if everything fails."""
        return f"""You are a helpful AI assistant for appointment scheduling.

Current customer: {name} (WhatsApp ID: {wa_id})
Current date: {current_date}
Current year: {current_year}

You can help with scheduling appointments using the calendar tools available.
Always be polite and professional.
"""


# Global instance
prompt_builder = PromptBuilder()
