from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
from langchain.tools import tool
from .calendar_service import GoogleCalendarService, calendar_service
from ..database.customer_service import customer_service


def get_calendar_service(business_context: Optional[Dict] = None) -> GoogleCalendarService:
    """Get calendar service instance, preferring business-specific credentials if available."""
    try:
        return GoogleCalendarService.from_business_context(business_context)
    except Exception as e:
        logging.warning(f"[CALENDAR] Failed to create business-specific service: {e}, using fallback")
        return calendar_service

def get_max_concurrent(business_context: Optional[Dict] = None) -> int:
    """Get max_concurrent appointments from business settings, with fallback to 2."""
    if business_context and 'business' in business_context:
        settings = business_context['business'].get('settings', {})
        appointment_settings = settings.get('appointment_settings', {})
        max_concurrent = appointment_settings.get('max_concurrent', 2)
        logging.info(f"[CONFIG] Using max_concurrent={max_concurrent} from business settings")
        return max_concurrent
    logging.info(f"[CONFIG] No business context, using default max_concurrent=2")
    return 2




def check_overlapping_events(start_time: str, end_time: str, business_context: Optional[Dict] = None) -> tuple[bool, int, str]:
    """
    Check if there are overlapping events at the requested time.

    Args:
        start_time: Start time in ISO format
        end_time: End time in ISO format
        business_context: Business context with settings

    Returns:
        Tuple of (has_overlap, event_count, message)
    """
    try:
        # Get calendar service for this business
        cal_service = get_calendar_service(business_context)

        # Get all events for the day
        events = cal_service.list_events(max_results=50)

        if not events or (len(events) == 1 and isinstance(events[0], dict) and "message" in events[0]):
            logging.info(f"[OVERLAP] No existing events found")
            return False, 0, "No existing events"

        # Parse the requested time range (expecting normalized format)
        start_dt = datetime.fromisoformat(start_time.replace('Z', ''))
        end_dt = datetime.fromisoformat(end_time.replace('Z', ''))

        overlapping_events = []

        for event in events:
            if isinstance(event, dict) and 'start' in event and 'end' in event:
                try:
                    # Parse event start and end times
                    event_start_str = event['start']
                    event_end_str = event['end']

                    # Handle different time formats
                    if 'T' not in event_start_str or 'T' not in event_end_str:
                        # All-day event, skip
                        continue

                    # Parse datetime strings, handling timezone info
                    def clean_datetime_string(dt_str):
                        """Clean datetime string for parsing"""
                        clean = dt_str.replace('Z', '')
                        # Remove timezone offset for simple comparison
                        if '+' in clean:
                            clean = clean.split('+')[0]
                        elif '-' in clean and 'T' in clean:
                            # Handle negative timezone offsets like -05:00
                            t_index = clean.index('T')
                            date_part = clean[:t_index]
                            time_part = clean[t_index:]
                            if time_part.count('-') > 0:
                                # Remove timezone offset from time part
                                time_part = time_part.split('-')[0]
                                clean = date_part + time_part
                        return clean

                    event_start_clean = clean_datetime_string(event_start_str)
                    event_end_clean = clean_datetime_string(event_end_str)

                    event_start_dt = datetime.fromisoformat(event_start_clean)
                    event_end_dt = datetime.fromisoformat(event_end_clean)

                    # Check for overlap
                    if (start_dt < event_end_dt and end_dt > event_start_dt):
                        overlapping_events.append(event)

                except Exception as e:
                    logging.warning(f"[OVERLAP] Error parsing event time: {e}")
                    continue

        event_count = len(overlapping_events)
        max_concurrent = get_max_concurrent(business_context)  # Get from business settings
        has_overlap = event_count >= max_concurrent

        if has_overlap:
            event_names = [event.get('summary', 'Unknown') for event in overlapping_events]
            message = f"Ya hay {event_count} eventos programados en ese horario: {', '.join(event_names)}. Máximo permitido: {max_concurrent} eventos simultáneos."
        else:
            message = f"Disponibilidad confirmada. Eventos actuales en ese horario: {event_count}"

        logging.info(f"[OVERLAP] Check result: {event_count} overlapping events, max_concurrent={max_concurrent}, has_overlap={has_overlap}")
        return has_overlap, event_count, message

    except Exception as e:
        logging.error(f"[ERROR] Error checking overlapping events: {e}")
        return False, 0, f"Error checking availability: {str(e)}"





@tool
def get_available_slots(date: str = "", time_range: str = "morning", injected_business_context: dict = None) -> str:
    """
    Get available time slots for appointments.

    Args:
        date: Date in YYYY-MM-DD format (default: tomorrow)
        time_range: "morning" (8AM-12PM), "afternoon" (12PM-5PM), "evening" (5PM-8PM), or "all"

    Returns:
        String with available time slots
    """
    logging.warning(f"[CALENDAR] Tool called: get_available_slots with date='{date}', time_range='{time_range}'")
    logging.warning(f"[DEBUG] injected_business_context type: {type(injected_business_context)}, is None: {injected_business_context is None}")
    try:
        from datetime import datetime, timedelta

        # Get business context from injected parameter
        business_context = injected_business_context

        # If no date provided, use tomorrow
        if not date or date == "":
            tomorrow = datetime.now() + timedelta(days=1)
            date = tomorrow.strftime('%Y-%m-%d')

        # Get business hours from database
        business_hours = {}
        if business_context and 'business' in business_context:
            settings = business_context['business'].get('settings', {})
            business_hours = settings.get('business_hours', {})
            logging.warning(f"[CONFIG] Using business_hours from database: {list(business_hours.keys())}")
        else:
            logging.warning(f"[CONFIG] No business_hours available, business_context={business_context is not None}")

        # Determine day of week for the requested date
        date_obj = datetime.strptime(date, '%Y-%m-%d')

        # Map weekday number to English day name (database uses English keys)
        weekday_map = {
            0: 'monday',
            1: 'tuesday',
            2: 'wednesday',
            3: 'thursday',
            4: 'friday',
            5: 'saturday',
            6: 'sunday'
        }
        day_name = weekday_map[date_obj.weekday()]

        day_hours = business_hours.get(day_name, {})
        logging.warning(f"[CONFIG] Date {date} is {day_name}, day_hours: {day_hours}")
        if day_hours.get('open') == 'closed':
            return f"❌ Lo siento, estamos cerrados los {day_name}s. ¿Te gustaría agendar para otro día?"

        # Get open/close times from database or use defaults
        open_time = day_hours.get('open', '08:00')  # Default 8:00 AM
        close_time = day_hours.get('close', '19:00')  # Default 7:00 PM

        # Parse hours (format: "HH:MM" in 24-hour)
        try:
            open_hour = int(open_time.split(':')[0])
            close_hour = int(close_time.split(':')[0])
        except:
            logging.warning(f"[CONFIG] Invalid time format in business_hours, using defaults")
            open_hour = 8
            close_hour = 19

        # Generate hourly slots based on business hours
        all_slots = []
        for hour in range(open_hour, close_hour):
            if hour == 0:
                all_slots.append("12:00 AM")
            elif hour < 12:
                all_slots.append(f"{hour}:00 AM")
            elif hour == 12:
                all_slots.append("12:00 PM")
            else:
                all_slots.append(f"{hour - 12}:00 PM")

        # Filter slots based on time_range
        if time_range == "morning":
            slots = [s for s in all_slots if any(h in s for h in ["8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM"])]
        elif time_range == "afternoon":
            slots = [s for s in all_slots if any(h in s for h in ["12:00 PM", "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM"])]
        elif time_range == "evening":
            slots = [s for s in all_slots if "PM" in s and not any(h in s for h in ["12:00 PM", "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM"])]
        else:  # "all"
            slots = all_slots

        if not slots:
            return f"❌ No hay horarios disponibles en la {time_range} para {date}. ¿Te gustaría probar otro horario?"

        # Get calendar service for this business
        cal_service = get_calendar_service(business_context)

        # Get existing events for the date
        events = cal_service.list_events(max_results=50)

        # Check which slots are available (not more than max_concurrent events at same time)
        max_concurrent = get_max_concurrent(business_context)  # Get from business settings
        available_slots = []

        for slot in slots:
            # Convert slot time to datetime for comparison
            slot_time = datetime.strptime(f"{date} {slot}", "%Y-%m-%d %I:%M %p")
            slot_end = slot_time + timedelta(hours=1)

            # Count overlapping events for this slot
            overlapping_count = 0
            for event in events:
                if isinstance(event, dict) and 'start' in event:
                    try:
                        event_start_str = event['start']
                        if 'T' in event_start_str:
                            event_start = datetime.fromisoformat(event_start_str.replace('Z', '').split('T')[0] + 'T' + event_start_str.split('T')[1].split('-')[0])
                            event_end = event_start + timedelta(hours=1)

                            # Check for overlap
                            if (slot_time < event_end and slot_end > event_start):
                                overlapping_count += 1
                    except Exception as e:
                        logging.warning(f"[AVAILABLE] Error parsing event time: {e}")
                        continue

            # Slot is available if less than max_concurrent events overlap
            if overlapping_count < max_concurrent:
                available_slots.append(slot)

        if available_slots:
            logging.warning(f"[CALENDAR] available_slots: {available_slots}")
            slots_text = ", ".join(available_slots)
            result = f"📅 Horarios disponibles para {date} ({time_range}):\n\n🕐 {slots_text}\n\n¿Cuál te gustaría?"
            logging.warning(f"[CALENDAR] get_available_slots completed: {len(available_slots)} slots available")
        else:
            result = f"❌ Lo siento, no hay horarios disponibles para {date} en la {time_range}. ¿Te gustaría probar otro día o horario?"
            logging.warning(f"[CALENDAR] get_available_slots completed: no slots available")

        return result

    except Exception as e:
        logging.error(f"[ERROR] Error getting available slots: {e}")
        return f"Error getting available slots: {str(e)}"


@tool
def schedule_appointment(whatsapp_id: str, summary: str, start_time: str, end_time: str,
                        customer_name: str = "", customer_age: str = "",
                        description: str = "", location: str = "",
                        injected_business_context: dict = None) -> str:
    """
    Schedule a new appointment for a user and save their customer information.

    Args:
        whatsapp_id: WhatsApp ID of the user
        summary: Title/summary of the appointment (e.g., "Corte y barba")
        start_time: Start time in ISO format (e.g., "2025-01-15T10:00:00")
        end_time: End time in ISO format (e.g., "2025-01-15T11:00:00")
        customer_name: Customer's full name (optional, will be saved to database)
        customer_age: Customer's age (optional, will be saved to database)
        description: Description of the appointment
        location: Location (will use business address from database if not provided)

    Returns:
        String message about the scheduled appointment
    """
    logging.warning(f"[CALENDAR] Tool called: schedule_appointment for user {whatsapp_id}, summary='{summary}', start_time='{start_time}', customer_name='{customer_name}', customer_age='{customer_age}'")

    try:
        from datetime import datetime

        # Get business context from injected parameter
        business_context = injected_business_context

        # Validate appointment time is within business hours
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', ''))
            day_name = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'][start_dt.weekday()]

            if business_context and 'business' in business_context:
                settings = business_context['business'].get('settings', {})
                business_hours = settings.get('business_hours', {})
                day_hours = business_hours.get(day_name, {})

                # Check if closed
                if day_hours.get('open') == 'closed':
                    return f"❌ Lo siento, estamos cerrados los {day_name}s. Por favor elige otro día."

                # Check if within business hours
                if day_hours:
                    open_time = day_hours.get('open', '00:00')
                    close_time = day_hours.get('close', '23:59')

                    requested_hour = start_dt.hour
                    requested_minute = start_dt.minute

                    open_hour = int(open_time.split(':')[0])
                    open_minute = int(open_time.split(':')[1]) if ':' in open_time else 0
                    close_hour = int(close_time.split(':')[0])
                    close_minute = int(close_time.split(':')[1]) if ':' in close_time else 0

                    requested_time_minutes = requested_hour * 60 + requested_minute
                    open_time_minutes = open_hour * 60 + open_minute
                    close_time_minutes = close_hour * 60 + close_minute

                    if requested_time_minutes < open_time_minutes or requested_time_minutes >= close_time_minutes:
                        return f"❌ Lo siento, el horario solicitado ({start_dt.strftime('%I:%M %p')}) está fuera de nuestro horario de atención. Los {day_name}s atendemos de {open_time} a {close_time}. Por favor elige un horario dentro de este rango."
        except Exception as e:
            logging.warning(f"[VALIDATION] Error validating business hours: {e}")

        # Get location from business settings if not provided
        if not location:
            if business_context and 'business' in business_context:
                settings = business_context['business'].get('settings', {})
                location = settings.get('address', 'Location TBD')
                logging.info(f"[CONFIG] Using location from database: {location}")
            else:
                location = "Location TBD"

        # Save customer information if provided
        if customer_name and customer_name.strip():
            customer_age_int = None
            if customer_age and customer_age.strip() and customer_age.strip().isdigit():
                customer_age_int = int(customer_age.strip())
                logging.warning(f"[CUSTOMER] Parsed age: {customer_age_int}")

            customer_info = customer_service.create_or_update_customer(
                whatsapp_id=whatsapp_id,
                name=customer_name.strip(),
                age=customer_age_int
            )

            if customer_info:
                logging.warning(f"[CUSTOMER] Successfully saved customer info: {customer_name}")
            else:
                logging.warning(f"[CUSTOMER] Failed to save customer info for {whatsapp_id}")

        # Normalize datetime format
        def normalize_datetime(dt_str):
            clean = dt_str.replace('Z', '')
            if '+' in clean:
                clean = clean.split('+')[0]
            elif '-' in clean and 'T' in clean:
                t_index = clean.index('T')
                date_part = clean[:t_index]
                time_part = clean[t_index:]
                if time_part.count('-') > 0:
                    time_part = time_part.split('-')[0]
                    clean = date_part + time_part
            return clean

        normalized_start = normalize_datetime(start_time)
        normalized_end = normalize_datetime(end_time)

        # Validate datetime format
        try:
            datetime.fromisoformat(normalized_start)
            datetime.fromisoformat(normalized_end)
        except ValueError as e:
            logging.error(f"[ERROR] Datetime validation failed: {e}")
            return f"❌ Formato de fecha/hora inválido. Error: {str(e)}"

        # Check for overlapping events (max 2 events at same time)
        has_overlap, event_count, overlap_message = check_overlapping_events(normalized_start, normalized_end, business_context)

        if has_overlap:
            logging.warning(f"[CALENDAR] Cannot create appointment - too many overlapping events: {overlap_message}")
            return f"❌ No se puede agendar la cita. {overlap_message}"

        # Include WhatsApp ID in description
        full_description = f"{description}\n[WhatsApp ID: {whatsapp_id}]" if description else f"[WhatsApp ID: {whatsapp_id}]"

        # Get calendar service for this business
        cal_service = get_calendar_service(business_context)

        # Create the appointment
        event = cal_service.create_event(
            summary=summary,
            start_time=normalized_start,
            end_time=normalized_end,
            description=full_description,
            location=location
        )

        if event and 'id' in event:
            event_id = event['id']
            logging.warning(f"[CALENDAR] schedule_appointment completed successfully: {event_id}")
            return f"✅ Tu cita '{summary}' ha sido agendada exitosamente para el {normalized_start.split('T')[0]} a las {datetime.fromisoformat(normalized_start).strftime('%I:%M %p')}, parce! 📅"
        else:
            logging.error(f"[ERROR] Failed to create appointment")
            return "❌ No se pudo crear la cita. Por favor, intenta de nuevo."

    except Exception as e:
        logging.error(f"[ERROR] Error scheduling appointment: {e}")
        return f"❌ Error agendando la cita: {str(e)}"


@tool
def reschedule_appointment(whatsapp_id: str, new_start_time: str, new_end_time: str,
                          appointment_selector: str = "latest", injected_business_context: dict = None) -> str:
    """
    Reschedule an existing appointment for a user.

    Args:
        whatsapp_id: WhatsApp ID of the user
        new_start_time: New start time in ISO format (e.g., "2025-01-15T14:00:00")
        new_end_time: New end time in ISO format (e.g., "2025-01-15T15:00:00")
        appointment_selector: Which appointment to reschedule ("latest", "today", "tomorrow", or specific service like "corte")

    Returns:
        String message about the rescheduled appointment
    """
    logging.warning(f"[CALENDAR] Tool called: reschedule_appointment for user {whatsapp_id}, new_start_time='{new_start_time}', selector='{appointment_selector}'")

    try:
        # Get business context from injected parameter
        business_context = injected_business_context

        # Get calendar service for this business
        cal_service = get_calendar_service(business_context)

        # First, find the user's appointments
        events = cal_service.list_events(max_results=50)
        user_appointments = []

        for event in events:
            if isinstance(event, dict) and 'start' in event and 'summary' in event:
                event_description = event.get('description', '')
                if whatsapp_id in event_description:
                    user_appointments.append(event)

        if not user_appointments:
            return f"❌ No se encontraron citas para reagendar."

        # Select which appointment to reschedule
        target_appointment = None

        if appointment_selector == "latest" or appointment_selector == "":
            target_appointment = user_appointments[0]  # Most recent
        else:
            # Search by service or other criteria
            for apt in user_appointments:
                if appointment_selector.lower() in apt['summary'].lower():
                    target_appointment = apt
                    break

        if not target_appointment:
            return f"❌ No se encontró una cita que coincida con '{appointment_selector}'."

        # Normalize new times
        def normalize_datetime(dt_str):
            clean = dt_str.replace('Z', '')
            if '+' in clean:
                clean = clean.split('+')[0]
            elif '-' in clean and 'T' in clean:
                t_index = clean.index('T')
                date_part = clean[:t_index]
                time_part = clean[t_index:]
                if time_part.count('-') > 0:
                    time_part = time_part.split('-')[0]
                    clean = date_part + time_part
            return clean

        normalized_start = normalize_datetime(new_start_time)
        normalized_end = normalize_datetime(new_end_time)

        # Validate datetime format
        try:
            datetime.fromisoformat(normalized_start)
            datetime.fromisoformat(normalized_end)
        except ValueError as e:
            logging.error(f"[ERROR] Datetime validation failed: {e}")
            return f"❌ Formato de fecha/hora inválido. Error: {str(e)}"

        # Update the appointment
        event_id = target_appointment['id']
        event_summary = target_appointment.get('summary', 'Cita')

        logging.warning(f"[CALENDAR] Updating appointment {event_id} from {target_appointment.get('start')} to {normalized_start}")

        updated_event = cal_service.update_event(
            event_id=event_id,
            start_time=normalized_start,
            end_time=normalized_end
        )

        if updated_event:
            logging.warning(f"[CALENDAR] reschedule_appointment completed successfully: {event_id}")
            return f"✅ Tu cita '{event_summary}' ha sido reagendada exitosamente para el {normalized_start.split('T')[0]} a las {datetime.fromisoformat(normalized_start).strftime('%I:%M %p')}, parce! 📅"
        else:
            return "❌ No se pudo reagendar la cita. Por favor, intenta de nuevo."

    except Exception as e:
        logging.error(f"[ERROR] Error rescheduling appointment: {e}")
        return f"❌ Error reagendando la cita: {str(e)}"


@tool
def cancel_appointment(whatsapp_id: str, appointment_selector: str = "latest", injected_business_context: dict = None) -> str:
    """
    Cancel an existing appointment for a user.

    Args:
        whatsapp_id: WhatsApp ID of the user
        appointment_selector: Which appointment to cancel ("latest", "today", "tomorrow", or specific service like "corte")

    Returns:
        String message about the cancelled appointment
    """
    logging.warning(f"[CALENDAR] Tool called: cancel_appointment for user {whatsapp_id}, selector='{appointment_selector}'")

    try:
        # Get business context from injected parameter
        business_context = injected_business_context

        # Get calendar service for this business
        cal_service = get_calendar_service(business_context)

        # First, find the user's appointments
        events = cal_service.list_events(max_results=50)
        user_appointments = []

        for event in events:
            if isinstance(event, dict) and 'start' in event and 'summary' in event:
                event_description = event.get('description', '')
                if whatsapp_id in event_description:
                    user_appointments.append(event)

        if not user_appointments:
            return f"❌ No se encontraron citas para cancelar."

        # Select which appointment to cancel
        target_appointment = None

        if appointment_selector == "latest" or appointment_selector == "":
            target_appointment = user_appointments[0]  # Most recent
        else:
            # Search by service or other criteria
            for apt in user_appointments:
                if appointment_selector.lower() in apt['summary'].lower():
                    target_appointment = apt
                    break

        if not target_appointment:
            return f"❌ No se encontró una cita que coincida con '{appointment_selector}'."

        # Cancel the appointment
        event_id = target_appointment['id']
        event_summary = target_appointment.get('summary', 'Cita')
        event_start = target_appointment.get('start', 'fecha no disponible')

        logging.warning(f"[CALENDAR] Cancelling appointment {event_id}: {event_summary}")

        success = cal_service.delete_event(event_id)

        if success:
            logging.warning(f"[CALENDAR] cancel_appointment completed successfully: {event_id}")
            return f"✅ Tu cita '{event_summary}' programada para {event_start} ha sido cancelada exitosamente, parce. Si necesitas reagendar, aquí estoy para ayudarte! 📅"
        else:
            return "❌ No se pudo cancelar la cita. Por favor, intenta de nuevo."

    except Exception as e:
        logging.error(f"[ERROR] Error cancelling appointment: {e}")
        return f"❌ Error cancelando la cita: {str(e)}"


# List of all calendar tools
calendar_tools = [
    get_available_slots,
    schedule_appointment,
    reschedule_appointment,
    cancel_appointment,
]