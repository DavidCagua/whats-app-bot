"""
Prompt Builder Service
Generates dynamic AI system prompts from business configuration.
Combines minimal core template (tool definitions) with admin-editable business prompt.
"""

import logging
from typing import Optional, Dict
from .business_config_service import business_config_service

# Minimal core template - just tool definitions and variable injection
# Admin controls everything else via database
CORE_TEMPLATE = """
---

### Available Calendar Tools

You have access to these calendar management tools:

- **schedule_appointment**(whatsapp_id, summary, start_time, end_time, customer_name, customer_age)
  Creates a new appointment in the calendar

- **get_available_slots**(date, time_range)
  Checks available time slots for appointments

- **reschedule_appointment**(whatsapp_id, new_start_time, new_end_time)
  Moves an existing appointment to a new time

- **cancel_appointment**(whatsapp_id)
  Cancels an existing appointment

---

### Current Context

- **Customer**: {name}
- **WhatsApp ID**: {wa_id}
- **Current Date**: {current_date} (DD/MM/YYYY)
- **Current Year**: {current_year}
- **Timezone**: {timezone}

---

### Business Information

{business_info}

---
"""


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

            # Get admin-editable prompt from database
            admin_prompt = ""
            if business_context:
                settings = business_context.get('business', {}).get('settings', {})
                admin_prompt = settings.get('ai_prompt', '')

            # If no custom prompt, use a generic one
            if not admin_prompt:
                admin_prompt = self._get_default_prompt()
                logging.warning("[PROMPT] No custom ai_prompt found, using default")

            # Replace variables in admin prompt
            filled_admin_prompt = admin_prompt.format(
                business_name=business_info.get('business_name', 'Business'),
                city=business_info.get('city', ''),
                state=business_info.get('state', ''),
                country=business_info.get('country', 'Colombia'),
                timezone=business_info.get('timezone', 'UTC'),
                max_concurrent=max_concurrent,
                current_date=current_date,
                current_year=current_year,
                name=name,
                wa_id=wa_id
            )

            # Get formatted business information for prompt
            business_info_text = business_config_service.get_services_text(business_context)
            business_info_text += "\n\n" + business_config_service.get_hours_text(business_context)
            business_info_text += "\n\n" + business_config_service.get_location_info(business_context)
            business_info_text += "\n\n" + business_config_service.get_payment_methods_text(business_context)

            promotions = business_config_service.get_promotions_text(business_context)
            if promotions:
                business_info_text += "\n\n" + promotions

            # Assemble final prompt: Admin prompt + Core template
            final_prompt = filled_admin_prompt + CORE_TEMPLATE.format(
                name=name,
                wa_id=wa_id,
                current_date=current_date,
                current_year=current_year,
                timezone=business_info.get('timezone', 'UTC'),
                business_info=business_info_text
            )

            logging.info(f"[PROMPT] Generated dynamic prompt (length: {len(final_prompt)} chars)")
            return final_prompt

        except Exception as e:
            logging.error(f"[PROMPT] Error building system prompt: {e}")
            # Return a safe fallback
            return self._get_fallback_prompt(name, wa_id, current_date, current_year)

    def _get_default_prompt(self) -> str:
        """Default prompt when none is configured."""
        return """Eres un asistente virtual amigable para {business_name}.

Tu función es ayudar a los clientes con:
- Información sobre servicios y precios
- Agendar citas
- Responder preguntas frecuentes

Usa un tono profesional y amigable.

REGLAS DE CITAS:
- Máximo {max_concurrent} citas al mismo tiempo
- Siempre confirma con fecha y hora exacta
- Formato: "✅ Tu cita está agendada para el [fecha] a las [hora] para [servicio], [nombre]"

Cliente actual: {name} (ID: {wa_id})
Fecha de hoy: {current_date}
Año: {current_year}
"""

    def _get_fallback_prompt(self, name: str, wa_id: str, current_date: str, current_year: int) -> str:
        """Emergency fallback prompt if everything fails."""
        return f"""You are a helpful AI assistant.

Current customer: {name} (ID: {wa_id})
Current date: {current_date}
Current year: {current_year}

You can help with scheduling appointments using the calendar tools available.
"""


# Global instance
prompt_builder = PromptBuilder()
