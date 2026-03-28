"""
Prompt Builder Service
Generates dynamic AI system prompts from business configuration.
Admin prompts are plain text with no variable injection.
All context (business, customer, runtime) is auto-generated and appended.
"""

import logging
from typing import Optional, Dict

from .business_config_service import business_config_service
from .staff_service import staff_service
from ..database.booking_service import booking_service


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
                name=name,
                wa_id=wa_id,
                current_date=current_date,
                current_year=current_year
            )

            # Build business info section (services, location, etc.; staff/hours from DB tables)
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
        name: str,
        wa_id: str,
        current_date: str,
        current_year: int
    ) -> str:
        """
        Build context section with business, customer, and runtime information.
        This replaces the old CORE_TEMPLATE's context variables.
        """
        context = "### IDIOMA / LANGUAGE\n"
        context += "- Detecta el idioma del mensaje del cliente (español o inglés).\n"
        context += "- Si el cliente escribe en inglés, responde SIEMPRE en inglés con un tono profesional y amigable.\n"
        context += "- Si el cliente escribe en español, responde en español (con el estilo y expresiones definidos en el prompt).\n"
        context += "- Mantén el mismo idioma durante toda la conversación.\n\n"

        context += "### CONTEXTO ACTUAL\n\n"

        # Business context
        context += "**Negocio:**\n"
        context += f"- Nombre: {business_info.get('business_name', 'Business')}\n"
        context += f"- Ubicación: {business_info.get('city', '')}, {business_info.get('state', '')}, {business_info.get('country', 'Colombia')}\n"

        phone = business_info.get('phone', '')
        if phone:
            context += f"- Teléfono: {phone}\n"

        context += f"- Zona horaria: {business_info.get('timezone', 'UTC')}\n"

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
        Build business information section (services, location, etc.).
        Staff and business hours come from staff_members + business_availability (not settings JSON).
        """
        sections = []

        # Services and prices
        services = business_config_service.get_services_text(business_context)
        if services:
            sections.append(services)

        business_id = (
            str(business_context.get("business_id"))
            if business_context and business_context.get("business_id")
            else None
        )
        if business_id:
            hours = self._build_hours_from_availability(business_id)
            if hours:
                sections.append(hours)
            staff = staff_service.get_staff_text_for_prompt(business_id)
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

    def _build_hours_from_availability(self, business_id: str) -> str:
        """Format business_availability rows for the system prompt (Sunday=0 … Saturday=6)."""
        rules = booking_service.get_availability(business_id)
        if not rules:
            return (
                "🕐 **HORARIOS DE ATENCIÓN**\n\n"
                "No hay horarios cargados en el sistema para este negocio "
                "(tabla business_availability)."
            )

        day_names = [
            "Domingo",
            "Lunes",
            "Martes",
            "Miércoles",
            "Jueves",
            "Viernes",
            "Sábado",
        ]
        lines = [
            "🕐 **HORARIOS DE ATENCIÓN** (configuración del sistema / business_availability)",
            "",
        ]
        for r in sorted(rules, key=lambda x: x.get("day_of_week", 0)):
            dow = r.get("day_of_week", 0)
            day_label = day_names[dow] if 0 <= dow <= 6 else f"Día {dow}"
            if not r.get("is_active", True):
                lines.append(f"• {day_label}: Cerrado")
                continue
            ot = r.get("open_time", "")
            ct = r.get("close_time", "")
            slot = r.get("slot_duration_minutes", 60)
            lines.append(
                f"• {day_label}: {ot} – {ct} (slots de {slot} min)"
            )
        return "\n".join(lines)

    def _get_default_prompt(self) -> str:
        """Default prompt when none is configured (no variables)."""
        return """Eres un asistente virtual amigable para el negocio.
Responde en el mismo idioma que use el cliente (español o inglés).

Tu función es ayudar a los clientes con:
- Información sobre servicios y precios
- Agendar citas
- Responder preguntas frecuentes

Usa un tono profesional y amigable.

REGLAS IMPORTANTES:
- La lista de servicios y los horarios de atención del system prompt vienen solo de la base de datos. Si una sección dice explícitamente que no hay datos cargados en el sistema para este negocio, NO inventes servicios, precios ni horarios: dilo con claridad y ofrece que contacten al negocio o que un administrador cargue la información.
- Verifica disponibilidad con las herramientas antes de confirmar citas (puedes filtrar por profesional o ver cupos para "cualquiera").
- Pregunta si el cliente prefiere un profesional concreto o "cualquiera" / el primero disponible.
- Si el cliente dice un nombre o apodo ("Gio", "Joel", "con Gio", "dale con Joel"): en schedule_appointment usa SIEMPRE staff_preference="specific" y staff_name_hint con el nombre corto (ej. "Gio"). El servidor asigna el UUID correcto; no adivines el UUID ni uses "anyone" si ya eligió persona.
- Si no hay nombre y acepta cualquiera: staff_preference="anyone".
- Opcional: staff_member_id (UUID) si lo copias de la lista; si entra en conflicto con lo que dijo el cliente, prioriza staff_name_hint.
- Si solo hay un profesional activo, puedes asignar sin preguntar.
- Siempre confirma con fecha, hora exacta, servicio, nombre del cliente y nombre del profesional asignado.
- Recolecta información del cliente de forma natural: nombre, edad, servicio deseado
"""

    def _get_fallback_prompt(self, name: str, wa_id: str, current_date: str, current_year: int) -> str:
        """Emergency fallback prompt if everything fails."""
        return f"""You are a helpful AI assistant for appointment scheduling.
Respond in the same language the customer uses (Spanish or English).

Current customer: {name} (WhatsApp ID: {wa_id})
Current date: {current_date}
Current year: {current_year}

You can help with scheduling appointments using the calendar tools available.
Do not invent services, prices, or business hours if the system prompt says none are loaded for this business; say so clearly.
Always be polite and professional.
"""


# Global instance
prompt_builder = PromptBuilder()
