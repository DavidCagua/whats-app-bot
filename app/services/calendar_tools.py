"""
Calendar tools for the BookingAgent.

Phase 4: Replaced Google Calendar backend with in-house booking system.
All operations now hit booking_service (DB) directly instead of Google Calendar API.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain.tools import tool

from ..database.booking_service import booking_service
from ..database.customer_service import customer_service

logger = logging.getLogger(__name__)


def _get_business_id(business_context: Optional[dict]) -> Optional[str]:
    """Extract business_id string from injected business context."""
    if not business_context:
        return None
    # Context shape: {"business_id": uuid, "business": {...}}
    bid = business_context.get("business_id")
    return str(bid) if bid else None


def _get_slot_duration(business_context: Optional[dict], date_str: str) -> int:
    """
    Get slot duration in minutes for a given date from availability rules.
    Falls back to 60 minutes if no rule found.
    """
    business_id = _get_business_id(business_context)
    if not business_id or not date_str:
        return 60

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        day_of_week_db = (target.weekday() + 1) % 7  # Mon=1 … Sun=0

        avail = booking_service.get_availability(business_id)
        for rule in avail:
            if rule.get("day_of_week") == day_of_week_db and rule.get("is_active"):
                return rule.get("slot_duration_minutes", 60)
    except Exception as e:
        logger.warning(f"[CALENDAR] Could not determine slot duration: {e}")

    return 60


@tool
def get_available_slots(date: str = "", time_range: str = "all",
                        injected_business_context: dict = None) -> str:
    """
    Get available time slots for appointments on a given date.

    Args:
        date: Date in YYYY-MM-DD format (default: tomorrow)
        time_range: "morning" (before 12PM), "afternoon" (12PM-5PM),
                    "evening" (after 5PM), or "all" (recommended)

    Returns:
        String listing available time slots for the requested date.
    """
    logger.warning(f"[CALENDAR] get_available_slots called: date='{date}', time_range='{time_range}'")

    try:
        business_id = _get_business_id(injected_business_context)
        if not business_id:
            return "❌ No se pudo determinar el negocio. Intenta de nuevo."

        # Default to tomorrow if no date given
        if not date:
            date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        slots = booking_service.get_available_slots(business_id, date)

        if slots is None:
            return "❌ Error consultando disponibilidad. Por favor intenta de nuevo."

        if not slots:
            return (
                f"❌ No hay horarios disponibles para {date}. "
                "El negocio puede estar cerrado ese día o no tiene horarios configurados. "
                "¿Te gustaría probar otro día?"
            )

        # Filter by time_range
        def slot_hour(s: dict) -> int:
            try:
                return int(s["start"].split(":")[0])
            except Exception:
                return 0

        if time_range == "morning":
            slots = [s for s in slots if slot_hour(s) < 12]
        elif time_range == "afternoon":
            slots = [s for s in slots if 12 <= slot_hour(s) < 17]
        elif time_range == "evening":
            slots = [s for s in slots if slot_hour(s) >= 17]

        available = [s for s in slots if s.get("available")]

        if not available:
            return (
                f"❌ No hay horarios disponibles en {time_range} para {date}. "
                "¿Te gustaría probar otro horario o día?"
            )

        slot_labels = [f"{s['start']} - {s['end']}" for s in available]
        slots_text = "\n".join(f"  • {label}" for label in slot_labels)

        logger.warning(f"[CALENDAR] get_available_slots: {len(available)} slots available for {date}")
        return (
            f"📅 Horarios disponibles para *{date}*:\n\n"
            f"{slots_text}\n\n"
            "¿Cuál te gustaría reservar?"
        )

    except Exception as e:
        logger.error(f"[CALENDAR] Error in get_available_slots: {e}")
        return f"❌ Error consultando disponibilidad: {str(e)}"


@tool
def schedule_appointment(whatsapp_id: str, summary: str, start_time: str, end_time: str,
                         customer_name: str = "", customer_age: str = "",
                         description: str = "", injected_business_context: dict = None) -> str:
    """
    Schedule a new appointment and save customer information.

    Args:
        whatsapp_id: WhatsApp ID of the customer
        summary: Service name / title (e.g. "Corte y barba")
        start_time: ISO format start time (e.g. "2025-03-25T10:00:00")
        end_time: ISO format end time (e.g. "2025-03-25T11:00:00")
        customer_name: Customer full name (saved to DB)
        customer_age: Customer age (optional)
        description: Extra notes for the booking

    Returns:
        Confirmation message or error.
    """
    logger.warning(
        f"[CALENDAR] schedule_appointment: whatsapp_id={whatsapp_id}, "
        f"summary='{summary}', start='{start_time}', end='{end_time}'"
    )

    try:
        business_id = _get_business_id(injected_business_context)
        if not business_id:
            return "❌ No se pudo determinar el negocio. Intenta de nuevo."

        # Parse & normalize times
        start_dt = _parse_dt(start_time)
        end_dt = _parse_dt(end_time)
        if not start_dt or not end_dt:
            return "❌ Formato de fecha/hora inválido. Usa YYYY-MM-DDTHH:MM:SS."

        # Validate within business hours using availability rules
        date_str = start_dt.strftime("%Y-%m-%d")
        slots = booking_service.get_available_slots(business_id, date_str)

        if slots is not None and len(slots) > 0:
            # Check if the requested start time falls in an available slot
            requested_start_hhmm = start_dt.strftime("%H:%M")
            matched_slot = next(
                (s for s in slots if s["start"] == requested_start_hhmm), None
            )
            if matched_slot and not matched_slot["available"]:
                return (
                    f"❌ El horario {requested_start_hhmm} ya está ocupado para {date_str}. "
                    "¿Quieres que te muestre los horarios disponibles?"
                )
        elif slots is not None and len(slots) == 0:
            logger.warning(
                f"[CALENDAR] No availability rules for {date_str}, proceeding anyway"
            )

        # Upsert customer
        customer = None
        if customer_name and customer_name.strip():
            age_int = None
            if customer_age and customer_age.strip().isdigit():
                age_int = int(customer_age.strip())
            customer = customer_service.create_or_update_customer(
                whatsapp_id=whatsapp_id,
                name=customer_name.strip(),
                age=age_int,
            )
            if customer:
                logger.info(f"[CALENDAR] Customer saved: {customer_name} (id={customer.get('id')})")
        else:
            customer = customer_service.get_customer_by_whatsapp_id(whatsapp_id)

        customer_id = customer["id"] if customer else None

        # Create booking
        notes = description or None
        booking = booking_service.create_booking({
            "business_id": business_id,
            "customer_id": customer_id,
            "service_name": summary,
            "start_at": start_dt.isoformat(),
            "end_at": end_dt.isoformat(),
            "status": "confirmed",
            "notes": notes,
            "created_via": "whatsapp",
        })

        if not booking:
            return "❌ No se pudo crear la cita. Por favor intenta de nuevo."

        display_date = start_dt.strftime("%d/%m/%Y")
        display_time = start_dt.strftime("%I:%M %p")
        logger.warning(f"[CALENDAR] Booking created: {booking['id']}")
        return (
            f"✅ ¡Cita agendada exitosamente!\n\n"
            f"📋 *{summary}*\n"
            f"📅 {display_date} a las {display_time}\n\n"
            "Si necesitas cancelar o reagendar, solo dímelo. ¡Hasta pronto!"
        )

    except Exception as e:
        logger.error(f"[CALENDAR] Error in schedule_appointment: {e}")
        return f"❌ Error agendando la cita: {str(e)}"


@tool
def reschedule_appointment(whatsapp_id: str, new_start_time: str, new_end_time: str,
                           appointment_selector: str = "latest",
                           injected_business_context: dict = None) -> str:
    """
    Reschedule an existing appointment for a customer.

    Args:
        whatsapp_id: WhatsApp ID of the customer
        new_start_time: New start time in ISO format
        new_end_time: New end time in ISO format
        appointment_selector: "latest" or partial service name (e.g. "corte")

    Returns:
        Confirmation message or error.
    """
    logger.warning(
        f"[CALENDAR] reschedule_appointment: whatsapp_id={whatsapp_id}, "
        f"new_start='{new_start_time}', selector='{appointment_selector}'"
    )

    try:
        business_id = _get_business_id(injected_business_context)

        # Find customer bookings
        bookings = booking_service.list_customer_bookings(
            whatsapp_id=whatsapp_id,
            business_id=business_id,
            upcoming_only=True,
        )

        if not bookings:
            return "❌ No se encontraron citas próximas para reagendar."

        # Select target booking
        target = _select_booking(bookings, appointment_selector)
        if not target:
            return f"❌ No se encontró una cita que coincida con '{appointment_selector}'."

        # Parse new times
        start_dt = _parse_dt(new_start_time)
        end_dt = _parse_dt(new_end_time)
        if not start_dt or not end_dt:
            return "❌ Formato de fecha/hora inválido. Usa YYYY-MM-DDTHH:MM:SS."

        updated = booking_service.update_booking(target["id"], {
            "start_at": start_dt.isoformat(),
            "end_at": end_dt.isoformat(),
            "status": "confirmed",
        })

        if not updated:
            return "❌ No se pudo reagendar la cita. Por favor intenta de nuevo."

        display_date = start_dt.strftime("%d/%m/%Y")
        display_time = start_dt.strftime("%I:%M %p")
        service = target.get("service_name", "Cita")
        logger.warning(f"[CALENDAR] Booking rescheduled: {target['id']}")
        return (
            f"✅ ¡Cita reagendada exitosamente!\n\n"
            f"📋 *{service}*\n"
            f"📅 {display_date} a las {display_time}"
        )

    except Exception as e:
        logger.error(f"[CALENDAR] Error in reschedule_appointment: {e}")
        return f"❌ Error reagendando la cita: {str(e)}"


@tool
def cancel_appointment(whatsapp_id: str, appointment_selector: str = "latest",
                       injected_business_context: dict = None) -> str:
    """
    Cancel an existing appointment for a customer.

    Args:
        whatsapp_id: WhatsApp ID of the customer
        appointment_selector: "latest" or partial service name (e.g. "corte")

    Returns:
        Confirmation message or error.
    """
    logger.warning(
        f"[CALENDAR] cancel_appointment: whatsapp_id={whatsapp_id}, "
        f"selector='{appointment_selector}'"
    )

    try:
        business_id = _get_business_id(injected_business_context)

        bookings = booking_service.list_customer_bookings(
            whatsapp_id=whatsapp_id,
            business_id=business_id,
            upcoming_only=True,
        )

        if not bookings:
            return "❌ No se encontraron citas próximas para cancelar."

        target = _select_booking(bookings, appointment_selector)
        if not target:
            return f"❌ No se encontró una cita que coincida con '{appointment_selector}'."

        updated = booking_service.update_booking(target["id"], {"status": "cancelled"})

        if not updated:
            return "❌ No se pudo cancelar la cita. Por favor intenta de nuevo."

        service = target.get("service_name", "Cita")
        start = target.get("start_at", "")
        try:
            display = datetime.fromisoformat(start).strftime("%d/%m/%Y a las %I:%M %p")
        except Exception:
            display = start

        logger.warning(f"[CALENDAR] Booking cancelled: {target['id']}")
        return (
            f"✅ Tu cita *{service}* programada para {display} ha sido cancelada. "
            "Si necesitas reagendar, aquí estoy para ayudarte 📅"
        )

    except Exception as e:
        logger.error(f"[CALENDAR] Error in cancel_appointment: {e}")
        return f"❌ Error cancelando la cita: {str(e)}"


# ============================================================================
# HELPERS
# ============================================================================

def _parse_dt(dt_str: str) -> Optional[datetime]:
    """Parse ISO datetime string, stripping tz offsets for naive comparison."""
    if not dt_str:
        return None
    try:
        clean = dt_str.replace("Z", "")
        if "+" in clean:
            clean = clean.split("+")[0]
        elif "-" in clean and "T" in clean:
            t_idx = clean.index("T")
            date_part = clean[:t_idx]
            time_part = clean[t_idx:]
            # Strip -HH:MM offset from time part if present
            if time_part.count("-") > 0:
                time_part = time_part.split("-")[0]
            clean = date_part + time_part
        return datetime.fromisoformat(clean)
    except Exception as e:
        logger.warning(f"[CALENDAR] _parse_dt failed for '{dt_str}': {e}")
        return None


def _select_booking(bookings: list, selector: str) -> Optional[dict]:
    """
    Select a booking from a list using a selector string.
    'latest' / '' → first upcoming; otherwise match by service_name substring.
    """
    if not bookings:
        return None
    if not selector or selector.strip().lower() in ("latest", ""):
        return bookings[0]
    selector_lower = selector.strip().lower()
    for b in bookings:
        service = (b.get("service_name") or "").lower()
        if selector_lower in service:
            return b
    return bookings[0]  # fallback to first


# List of all calendar tools (unchanged interface for booking_agent.py)
calendar_tools = [
    get_available_slots,
    schedule_appointment,
    reschedule_appointment,
    cancel_appointment,
]
