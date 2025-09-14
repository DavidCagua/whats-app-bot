from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
from langchain.tools import tool
from .calendar_service import calendar_service
from ..database.customer_service import customer_service




def check_overlapping_events(start_time: str, end_time: str) -> tuple[bool, int, str]:
    """
    Check if there are overlapping events at the requested time.

    Args:
        start_time: Start time in ISO format
        end_time: End time in ISO format

    Returns:
        Tuple of (has_overlap, event_count, message)
    """
    try:
        # Get all events for the day
        events = calendar_service.list_events(max_results=50)

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
        has_overlap = event_count >= 2  # Allow max 2 events at same time

        if has_overlap:
            event_names = [event.get('summary', 'Unknown') for event in overlapping_events]
            message = f"Ya hay {event_count} eventos programados en ese horario: {', '.join(event_names)}. Máximo permitido: 2 eventos simultáneos."
        else:
            message = f"Disponibilidad confirmada. Eventos actuales en ese horario: {event_count}"

        logging.info(f"[OVERLAP] Check result: {event_count} overlapping events, has_overlap={has_overlap}")
        return has_overlap, event_count, message

    except Exception as e:
        logging.error(f"[ERROR] Error checking overlapping events: {e}")
        return False, 0, f"Error checking availability: {str(e)}"





@tool
def get_available_slots(date: str = "", time_range: str = "morning") -> str:
    """
    Get available time slots for appointments.

    Args:
        date: Date in YYYY-MM-DD format (default: tomorrow)
        time_range: "morning" (8AM-12PM), "afternoon" (12PM-5PM), "evening" (5PM-8PM), or "all"

    Returns:
        String with available time slots
    """
    logging.info(f"[CALENDAR] Tool called: get_available_slots with date='{date}', time_range='{time_range}'")
    try:
        from datetime import datetime, timedelta

        # If no date provided, use tomorrow
        if not date or date == "":
            tomorrow = datetime.now() + timedelta(days=1)
            date = tomorrow.strftime('%Y-%m-%d')

        # Define time slots based on time_range
        if time_range == "morning":
            slots = ["8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM"]
        elif time_range == "afternoon":
            slots = ["12:00 PM", "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM"]
        elif time_range == "evening":
            slots = ["5:00 PM", "6:00 PM", "7:00 PM"]
        else:  # "all"
            slots = ["8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM", "12:00 PM", "1:00 PM", "2:00 PM", "3:00 PM", "4:00 PM", "5:00 PM", "6:00 PM", "7:00 PM"]

        # Get existing events for the date
        events = calendar_service.list_events(max_results=50)

        # Check which slots are available (not more than 2 events at same time)
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

            # Slot is available if less than 2 events overlap
            if overlapping_count < 2:
                available_slots.append(slot)

        if available_slots:
            slots_text = ", ".join(available_slots)
            result = f"📅 Horarios disponibles para {date} ({time_range}):\n\n🕐 {slots_text}\n\n¿Cuál te gustaría?"
            logging.info(f"[CALENDAR] get_available_slots completed: {len(available_slots)} slots available")
        else:
            result = f"❌ Lo siento, no hay horarios disponibles para {date} en la {time_range}. ¿Te gustaría probar otro día o horario?"
            logging.info(f"[CALENDAR] get_available_slots completed: no slots available")

        return result

    except Exception as e:
        logging.error(f"[ERROR] Error getting available slots: {e}")
        return f"Error getting available slots: {str(e)}"


@tool
def schedule_appointment(whatsapp_id: str, summary: str, start_time: str, end_time: str,
                        customer_name: str = "", customer_age: str = "",
                        description: str = "", location: str = "Calle 18 #25-30, Centro, Pasto") -> str:
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
        location: Location (defaults to barbería address)

    Returns:
        String message about the scheduled appointment
    """
    logging.warning(f"[CALENDAR] Tool called: schedule_appointment for user {whatsapp_id}, summary='{summary}', start_time='{start_time}', customer_name='{customer_name}', customer_age='{customer_age}'")

    try:
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
        has_overlap, event_count, overlap_message = check_overlapping_events(normalized_start, normalized_end)

        if has_overlap:
            logging.warning(f"[CALENDAR] Cannot create appointment - too many overlapping events: {overlap_message}")
            return f"❌ No se puede agendar la cita. {overlap_message}"

        # Include WhatsApp ID in description
        full_description = f"{description}\n[WhatsApp ID: {whatsapp_id}]" if description else f"[WhatsApp ID: {whatsapp_id}]"

        # Create the appointment
        event = calendar_service.create_event(
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
                          appointment_selector: str = "latest") -> str:
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
        # First, find the user's appointments
        events = calendar_service.list_events(max_results=50)
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

        updated_event = calendar_service.update_event(
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
def cancel_appointment(whatsapp_id: str, appointment_selector: str = "latest") -> str:
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
        # First, find the user's appointments
        events = calendar_service.list_events(max_results=50)
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

        success = calendar_service.delete_event(event_id)

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