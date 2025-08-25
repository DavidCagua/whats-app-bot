from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
from langchain.tools import tool
from .calendar_service import calendar_service
import pytz


def convert_to_colombia_timezone(dt_str: str) -> str:
    """
    Convert a datetime string to Colombia timezone (UTC-5).
    Treats the input time as local Colombia time and ensures it's properly formatted.

    Args:
        dt_str: Datetime string in ISO format (e.g., "2025-08-08T10:00:00")

    Returns:
        Datetime string in Colombia timezone with proper offset
    """
    try:
        # Clean the input string (remove Z if present)
        dt_str_clean = dt_str.replace('Z', '')

        # Parse the datetime
        dt = datetime.fromisoformat(dt_str_clean)

        # Add Colombia timezone info
        colombia_tz = pytz.timezone('America/Bogota')

        # Handle timezone-aware vs naive datetime
        if dt.tzinfo is None:
            # Naive datetime - localize it
            colombia_dt = colombia_tz.localize(dt)
        else:
            # Already timezone-aware - convert to Colombia timezone
            colombia_dt = dt.astimezone(colombia_tz)

        # Format as ISO string with timezone offset
        result = colombia_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
        logging.info(f"[TIMEZONE] Converted {dt_str} -> {result} (treated as local Colombia time)")
        return result
    except Exception as e:
        logging.error(f"[ERROR] Error converting timezone: {e}")
        return dt_str


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

        # Parse the requested time range
        colombia_tz = pytz.timezone('America/Bogota')

        # Parse start time
        start_dt_clean = start_time.replace('Z', '')
        start_dt = datetime.fromisoformat(start_dt_clean)
        if start_dt.tzinfo is None:
            start_dt = colombia_tz.localize(start_dt)
        else:
            start_dt = start_dt.astimezone(colombia_tz)

        # Parse end time
        end_dt_clean = end_time.replace('Z', '')
        end_dt = datetime.fromisoformat(end_dt_clean)
        if end_dt.tzinfo is None:
            end_dt = colombia_tz.localize(end_dt)
        else:
            end_dt = end_dt.astimezone(colombia_tz)

        overlapping_events = []

        for event in events:
            if isinstance(event, dict) and 'start' in event and 'end' in event:
                try:
                    # Parse event start and end times
                    event_start_str = event['start']
                    event_end_str = event['end']

                    # Handle different time formats
                    if 'T' in event_start_str:
                        event_start_dt = colombia_tz.localize(datetime.fromisoformat(event_start_str.replace('Z', '').split('T')[0] + 'T' + event_start_str.split('T')[1].split('-')[0]))
                    else:
                        # All-day event, skip
                        continue

                    if 'T' in event_end_str:
                        event_end_dt = colombia_tz.localize(datetime.fromisoformat(event_end_str.replace('Z', '').split('T')[0] + 'T' + event_end_str.split('T')[1].split('-')[0]))
                    else:
                        # All-day event, skip
                        continue

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
def list_calendar_events(max_results: int = 10) -> str:
    """
    List upcoming calendar events.

    Args:
        max_results: Maximum number of events to return (default: 10)

    Returns:
        String representation of upcoming calendar events
    """
    logging.info(f"[CALENDAR] Tool called: list_calendar_events with max_results={max_results}")
    try:
        events = calendar_service.list_events(max_results=max_results)
        if not events:
            return "No upcoming events found"

        # Format events as a readable string
        if len(events) == 1 and isinstance(events[0], dict) and "message" in events[0]:
            return events[0]["message"]

        event_list = []
        for event in events:
            if isinstance(event, dict) and 'summary' in event:
                start_time = event.get('start', 'Unknown time')
                event_list.append(f"• {event['summary']} ({start_time})")

        result = f"Upcoming events:\n" + "\n".join(event_list) if event_list else "No upcoming events found"
        logging.info(f"[CALENDAR] list_calendar_events completed successfully: {len(event_list)} events found")
        return result
    except Exception as e:
        logging.error(f"[ERROR] Error listing calendar events: {e}")
        return f"Failed to list events: {str(e)}"


@tool
def create_calendar_event(summary: str, start_time: str, end_time: str,
                         description: str = "", location: str = "") -> str:
    """
    Create a new calendar event.

    Args:
        summary: Title/summary of the event
        start_time: Start time in ISO format (e.g., "2024-01-15T10:00:00Z")
        end_time: End time in ISO format (e.g., "2024-01-15T11:00:00Z")
        description: Optional description of the event
        location: Optional location of the event

    Returns:
        String message about the created event or error
    """
    logging.info(f"[CALENDAR] Tool called: create_calendar_event with summary='{summary}', start_time='{start_time}', end_time='{end_time}'")
    try:
        # Convert times to Colombia timezone (treating input as local Colombia time)
        colombia_start_time = convert_to_colombia_timezone(start_time)
        colombia_end_time = convert_to_colombia_timezone(end_time)

        logging.info(f"[CALENDAR] Converted times - Start: {start_time} -> {colombia_start_time}, End: {end_time} -> {colombia_end_time}")

        # Check for overlapping events (max 2 events at same time)
        has_overlap, event_count, overlap_message = check_overlapping_events(start_time, end_time)

        if has_overlap:
            logging.warning(f"[CALENDAR] Cannot create event - too many overlapping events: {overlap_message}")
            return f"❌ No se puede agendar la cita. {overlap_message}"

        # Validate datetime format
        try:
            datetime.fromisoformat(colombia_start_time.replace('Z', '+00:00'))
            datetime.fromisoformat(colombia_end_time.replace('Z', '+00:00'))
        except ValueError:
            return "Invalid datetime format. Use ISO format (e.g., '2024-01-15T10:00:00Z')"

        event = calendar_service.create_event(
            summary=summary,
            start_time=colombia_start_time,
            end_time=colombia_end_time,
            description=description,
            location=location
        )

        if event and 'id' in event:
            event_id = event['id']
            event_url = event.get('htmlLink', 'No URL available')
            logging.info(f"[CALENDAR] create_calendar_event completed successfully: {event_id}")
            return f"✅ Event '{summary}' created successfully! Event ID: {event_id}\nEvent URL: {event_url}"
        else:
            logging.error(f"[ERROR] Failed to create event - no event ID returned")
            return "Failed to create event - no event ID returned"

    except Exception as e:
        logging.error(f"[ERROR] Error creating calendar event: {e}")
        return f"Failed to create event: {str(e)}"


@tool
def update_calendar_event(event_id: str, summary: str = None, start_time: str = None,
                         end_time: str = None, description: str = None, location: str = None) -> str:
    """
    Update an existing calendar event.

    Args:
        event_id: ID of the event to update
        summary: New title/summary of the event (optional)
        start_time: New start time in ISO format (optional)
        end_time: New end time in ISO format (optional)
        description: New description of the event (optional)
        location: New location of the event (optional)

    Returns:
        String message about the updated event or error
    """
    try:
        # Validate datetime format if provided
        if start_time:
            try:
                datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            except ValueError:
                return "Invalid start_time format. Use ISO format (e.g., '2024-01-15T10:00:00Z')"

        if end_time:
            try:
                datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            except ValueError:
                return "Invalid end_time format. Use ISO format (e.g., '2024-01-15T11:00:00Z')"

        event = calendar_service.update_event(
            event_id=event_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location
        )

        if event:
            return f"Event '{event['summary']}' updated successfully!"
        else:
            return "Failed to update event"

    except Exception as e:
        logging.error(f"Error updating calendar event: {e}")
        return f"Failed to update event: {str(e)}"


@tool
def delete_calendar_event(event_id: str) -> str:
    """
    Delete a calendar event.

    Args:
        event_id: ID of the event to delete

    Returns:
        String message about the deletion success or error
    """
    try:
        success = calendar_service.delete_event(event_id)
        if success:
            return f"Event {event_id} deleted successfully"
        else:
            return "Failed to delete event"

    except Exception as e:
        logging.error(f"Error deleting calendar event: {e}")
        return f"Failed to delete event: {str(e)}"


@tool
def get_calendar_event(event_id: str) -> str:
    """
    Get details of a specific calendar event.

    Args:
        event_id: ID of the event to retrieve

    Returns:
        String representation of the event details or error message
    """
    try:
        event = calendar_service.get_event(event_id)
        if event:
            return f"Event: {event['summary']}\nStart: {event['start']}\nEnd: {event['end']}\nDescription: {event.get('description', 'No description')}\nLocation: {event.get('location', 'No location')}"
        else:
            return "Event not found"

    except Exception as e:
        logging.error(f"Error getting calendar event: {e}")
        return f"Failed to get event: {str(e)}"


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


# List of all calendar tools
calendar_tools = [
    list_calendar_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
    get_calendar_event,
    get_available_slots,
]