"""
Calendar tools for the BookingAgent.

All operations hit booking_service (DB) directly.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from langchain.tools import tool

from ..database.booking_service import booking_service
from ..database.customer_service import customer_service
from .staff_service import staff_service

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


def _normalize_staff_id(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s else None


def _resolve_staff_id_by_hint(business_id: str, hint: str) -> tuple[Optional[str], Optional[str]]:
    """
    Map a customer's barber name fragment (e.g. "Gio", "Joel") to a single active staff UUID.

    Returns:
        (staff_id, None) on success
        (None, error_message) if none or ambiguous
    """
    hint_norm = (hint or "").strip().lower()
    if len(hint_norm) < 2:
        return (
            None,
            "El nombre del profesional es demasiado corto; usa al menos 2 letras.",
        )

    staff_list = staff_service.get_staff_by_business(business_id, active_only=True)
    if not staff_list:
        return None, "No hay profesionales activos."

    def norm_name(s: str) -> str:
        return (s or "").strip().lower()

    matches: list[dict] = []
    for s in staff_list:
        name = norm_name(s.get("name") or "")
        if not name:
            continue
        parts = name.split()
        first = parts[0] if parts else ""

        if name == hint_norm:
            matches.append(s)
            continue
        if first == hint_norm:
            matches.append(s)
            continue
        if len(hint_norm) >= 2 and first.startswith(hint_norm):
            matches.append(s)
            continue
        # Substring only for longer hints (avoids "el" matching "Joel")
        if len(hint_norm) >= 3 and hint_norm in name:
            matches.append(s)

    by_id: dict[str, dict] = {}
    for s in matches:
        by_id[s["id"]] = s
    uniq = list(by_id.values())

    if len(uniq) == 1:
        return uniq[0]["id"], None
    if not uniq:
        return (
            None,
            f"No encontré un profesional que coincida con «{hint.strip()}». "
            "Usa list_booking_staff y pide al cliente el nombre exacto de la lista.",
        )
    names = ", ".join(x["name"] for x in uniq)
    return (
        None,
        f"Varios profesionales coinciden con «{hint.strip()}»: {names}. Pide aclaración al cliente.",
    )


@tool
def list_booking_staff(injected_business_context: dict = None) -> str:
    """
    List active staff for this business (id, name, role) for booking and availability tools.

    Returns:
        Formatted list with staff UUIDs to use with schedule_appointment and get_available_slots.
    """
    business_id = _get_business_id(injected_business_context)
    if not business_id:
        return "❌ No se pudo determinar el negocio."

    staff_rows = staff_service.get_staff_by_business(business_id, active_only=True)
    if not staff_rows:
        return (
            "❌ Este negocio no tiene profesionales activos en el sistema; "
            "no se pueden agendar citas por WhatsApp hasta que se configure el equipo."
        )

    lines = ["👥 Profesionales disponibles:\n"]
    for s in staff_rows:
        lines.append(f"• `{s['id']}` — {s['name']} ({s['role']})")
    lines.append(
        '\nPara un profesional concreto: staff_preference="specific" y además '
        'staff_name_hint con el nombre que dijo el cliente (ej. "Gio", "Joel") '
        '— el sistema resuelve al UUID correcto. Opcional: staff_member_id (UUID). '
        'Para cualquiera disponible: staff_preference="anyone".'
    )
    return "\n".join(lines)


@tool
def get_available_slots(
    date: str = "",
    time_range: str = "all",
    staff_member_id: str = "",
    injected_business_context: dict = None,
) -> str:
    """
    Get available time slots for appointments on a given date.

    Args:
        date: Date in YYYY-MM-DD format (default: tomorrow)
        time_range: "morning" (before 12PM), "afternoon" (12PM-5PM),
                    "evening" (after 5PM), or "all" (recommended)
        staff_member_id: If provided, availability for that staff only.
            If empty, aggregate mode (slot free if at least one staff is free; shows who).

    Returns:
        String listing available time slots for the requested date.
    """
    sid = _normalize_staff_id(staff_member_id)
    logger.warning(
        f"[CALENDAR] get_available_slots called: date='{date}', time_range='{time_range}', "
        f"staff_member_id={sid!r}"
    )

    try:
        business_id = _get_business_id(injected_business_context)
        if not business_id:
            return "❌ No se pudo determinar el negocio. Intenta de nuevo."

        # Default to tomorrow if no date given
        if not date:
            date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        staff_rows = staff_service.get_staff_by_business(business_id, active_only=True)
        if not staff_rows:
            return (
                "❌ No hay profesionales activos; no se puede consultar disponibilidad. "
                "Contacta al negocio."
            )

        slots = booking_service.get_available_slots(business_id, date, staff_member_id=sid)

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

        id_to_name = {r["id"]: r["name"] for r in staff_rows}
        slot_lines = []
        for s in available:
            label = f"{s['start']} - {s['end']}"
            if sid:
                slot_lines.append(f"  • {label}")
            else:
                fids = s.get("free_staff_ids") or []
                names = [id_to_name.get(fid, fid[:8] + "…") for fid in fids]
                who = ", ".join(names) if names else "(nadie libre)"
                slot_lines.append(f"  • {label} — disponible con: {who}")

        slots_text = "\n".join(slot_lines)
        mode = f"profesional `{sid}`" if sid else "cualquier profesional disponible"

        logger.warning(f"[CALENDAR] get_available_slots: {len(available)} slots for {date} ({mode})")
        return (
            f"📅 Horarios disponibles para *{date}* ({mode}):\n\n"
            f"{slots_text}\n\n"
            "¿Cuál te gustaría reservar?"
        )

    except Exception as e:
        logger.error(f"[CALENDAR] Error in get_available_slots: {e}")
        return f"❌ Error consultando disponibilidad: {str(e)}"


@tool
def schedule_appointment(
    whatsapp_id: str,
    summary: str,
    start_time: str,
    end_time: str,
    customer_name: str = "",
    customer_age: str = "",
    description: str = "",
    staff_preference: str = "anyone",
    staff_member_id: str = "",
    staff_name_hint: str = "",
    injected_business_context: dict = None,
) -> str:
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
        staff_preference: "specific" or "anyone" (hint overrides to specific when set)
        staff_member_id: UUID when using specific without hint (optional if staff_name_hint set)
        staff_name_hint: Name the customer said (e.g. "Gio", "Joel", "con Gio") — resolved server-side to the correct staff UUID

    Returns:
        Confirmation message or error.
    """
    pref = (staff_preference or "anyone").strip().lower()
    sid_raw = _normalize_staff_id(staff_member_id)
    name_hint = (staff_name_hint or "").strip()

    logger.warning(
        f"[CALENDAR] schedule_appointment: whatsapp_id={whatsapp_id}, "
        f"summary='{summary}', start='{start_time}', end='{end_time}', "
        f"staff_preference={pref}, staff_member_id={sid_raw!r}, staff_name_hint={name_hint!r}"
    )

    try:
        business_id = _get_business_id(injected_business_context)
        if not business_id:
            return "❌ No se pudo determinar el negocio. Intenta de nuevo."

        staff_rows = staff_service.get_staff_by_business(business_id, active_only=True)
        if not staff_rows:
            return (
                "❌ No hay profesionales activos; no se puede agendar por WhatsApp. "
                "Contacta al negocio."
            )

        if pref not in ("specific", "anyone"):
            return '❌ staff_preference debe ser "specific" o "anyone".'

        # Customer-chosen name beats wrong model UUID / "anyone" slip
        if name_hint:
            # Strip common Spanish filler so "dale con Gio" → "Gio"
            lowered = name_hint.lower()
            prefixes = ("dale ", "con ", "para ", "quiero ", "el ", "la ")
            while True:
                hit = False
                for prefix in prefixes:
                    if lowered.startswith(prefix):
                        name_hint = name_hint[len(prefix) :].strip()
                        lowered = name_hint.lower()
                        hit = True
                        break
                if not hit:
                    break
            resolved_id, resolve_err = _resolve_staff_id_by_hint(business_id, name_hint)
            if resolve_err:
                return f"❌ {resolve_err}"
            if sid_raw and sid_raw != resolved_id:
                logger.warning(
                    f"[CALENDAR] staff_member_id {sid_raw} ignored; using name hint -> {resolved_id}"
                )
            sid_raw = resolved_id
            pref = "specific"

        if pref == "specific" and not sid_raw:
            return (
                "❌ Para un profesional específico usa staff_name_hint con el nombre que dijo el cliente "
                '(ej. "Gio") o staff_member_id (UUID de list_booking_staff). '
                "Nunca uses staff_preference anyone si el cliente ya eligió nombre."
            )

        if pref == "anyone" and len(staff_rows) == 1:
            sid_raw = staff_rows[0]["id"]
            pref = "specific"

        # Parse & normalize times
        start_dt = _parse_dt(start_time)
        end_dt = _parse_dt(end_time)
        if not start_dt or not end_dt:
            return "❌ Formato de fecha/hora inválido. Usa YYYY-MM-DDTHH:MM:SS."

        if end_dt <= start_dt:
            return "❌ La hora de fin debe ser después de la hora de inicio."

        # Attach UTC if naive for DB consistency
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        date_str = start_dt.strftime("%Y-%m-%d")
        slots = booking_service.get_available_slots(
            business_id,
            date_str,
            staff_member_id=sid_raw if pref == "specific" else None,
        )

        requested_start_hhmm = start_dt.strftime("%H:%M")
        matched_slot = None
        if slots is not None and len(slots) > 0:
            matched_slot = next((s for s in slots if s["start"] == requested_start_hhmm), None)
            if matched_slot and not matched_slot.get("available"):
                return (
                    f"❌ El horario {requested_start_hhmm} no está disponible para {date_str}. "
                    "¿Quieres que te muestre los horarios disponibles?"
                )
        if not matched_slot:
            return (
                f"❌ El horario {requested_start_hhmm} está fuera del horario de atención para {date_str}. "
                "¿Quieres que te muestre los horarios disponibles?"
            )

        chosen_staff_id: Optional[str] = None
        if pref == "specific":
            assert sid_raw
            if not booking_service.is_interval_free_for_staff(
                business_id, start_dt, end_dt, sid_raw
            ):
                return (
                    "❌ Ese profesional no está libre en el horario solicitado. "
                    "Pide horarios con get_available_slots y su staff_member_id."
                )
            chosen_staff_id = sid_raw
        else:
            chosen_staff_id = booking_service.pick_random_free_staff_for_interval(
                business_id, start_dt, end_dt
            )
            if not chosen_staff_id:
                return (
                    "❌ No hay ningún profesional libre en ese horario. "
                    "¿Te muestro otros horarios con get_available_slots?"
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

        staff_name = ""
        st = staff_service.get_staff_member(chosen_staff_id)
        if st:
            staff_name = st.get("name") or ""

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
            "staff_member_id": chosen_staff_id,
        })

        if not booking:
            return "❌ No se pudo crear la cita. Por favor intenta de nuevo."

        display_date = start_dt.strftime("%d/%m/%Y")
        display_time = start_dt.strftime("%I:%M %p")
        prof_line = f"👤 Profesional: *{staff_name}*\n" if staff_name else ""
        logger.warning(f"[CALENDAR] Booking created: {booking['id']} staff={chosen_staff_id}")
        return (
            f"✅ ¡Cita agendada exitosamente!\n\n"
            f"📋 *{summary}*\n"
            f"{prof_line}"
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
        if end_dt <= start_dt:
            return "❌ La hora de fin debe ser después de la hora de inicio."

        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        if not booking_service.is_within_business_hours(business_id, start_dt, end_dt):
            return (
                "❌ El nuevo horario está fuera del horario de atención del negocio. "
                "Pide horarios con get_available_slots antes de reagendar."
            )

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
    list_booking_staff,
    get_available_slots,
    schedule_appointment,
    reschedule_appointment,
    cancel_appointment,
]
