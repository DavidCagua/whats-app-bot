import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarService:
    def __init__(self):
        self.creds = None
        self.service = None
        self.enabled = True
        try:
            self._authenticate()
        except Exception as e:
            logging.warning(f"[CALENDAR] Google Calendar authentication failed: {e}")
            logging.warning("[CALENDAR] Calendar features will be disabled")
            self.enabled = False

    def _authenticate(self):
        """Authenticate with Google Calendar API using files or environment variables."""

        # Try environment variables first (for Railway/production)
        client_id = os.getenv('GOOGLE_CLIENT_ID')
        client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        refresh_token = os.getenv('GOOGLE_REFRESH_TOKEN')

        if client_id and client_secret and refresh_token:
            # Create credentials from environment variables
            logging.info("[CALENDAR] Using Google credentials from environment variables")
            self.creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES
            )
            # Refresh to get a valid access token
            try:
                self.creds.refresh(Request())
                logging.info("[CALENDAR] Successfully refreshed credentials from environment")
            except Exception as e:
                logging.error(f"[CALENDAR] Failed to refresh credentials: {e}")
                raise

        # Fallback to file-based authentication (for local development)
        elif os.path.exists('token.json'):
            logging.info("[CALENDAR] Using Google credentials from token.json file")
            self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)

            # If there are no (valid) credentials available, let the user log in.
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    client_secret_file = 'client_secret_873374688567-icspa759as15biuvdta695qv2mv7ldbp.apps.googleusercontent.com.json'
                    if os.path.exists(client_secret_file):
                        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
                        self.creds = flow.run_local_server(port=0)
                        # Save the credentials for the next run
                        with open('token.json', 'w') as token:
                            token.write(self.creds.to_json())
                    else:
                        raise FileNotFoundError("Google Calendar client secret file not found")
        else:
            # No credentials available
            raise ValueError("No Google Calendar credentials found (neither env vars nor token.json)")

        try:
            self.service = build('calendar', 'v3', credentials=self.creds)
            logging.info("[CALENDAR] Successfully authenticated with Google Calendar API")
        except HttpError as error:
            logging.error(f'[CALENDAR] An error occurred: {error}')
            raise

    def list_events(self, max_results: int = 10) -> List[Dict]:
        """List upcoming events from the primary calendar."""
        try:
            # Call the Calendar API
            now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            if not events:
                return []

            formatted_events = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                formatted_events.append({
                    'id': event['id'],
                    'summary': event.get('summary', 'No title'),
                    'start': start,
                    'end': end,
                    'description': event.get('description', ''),
                    'location': event.get('location', '')
                })

            return formatted_events

        except HttpError as error:
            logging.error(f'An error occurred: {error}')
            return []

    def create_event(self, summary: str, start_time: str, end_time: str,
                    description: str = "", location: str = "") -> Optional[Dict]:
        """Create a new calendar event."""
        try:
            event = {
                'summary': summary,
                'location': location,
                'description': description,
                'start': {
                    'dateTime': start_time,
                    'timeZone': 'America/Bogota',
                },
                'end': {
                    'dateTime': end_time,
                    'timeZone': 'America/Bogota',
                },
            }

            event = self.service.events().insert(calendarId='primary', body=event).execute()
            logging.info(f'Event created: {event.get("htmlLink")}')

            return {
                'id': event['id'],
                'summary': event.get('summary', 'No title'),
                'start': event['start'].get('dateTime'),
                'end': event['end'].get('dateTime'),
                'description': event.get('description', ''),
                'location': event.get('location', ''),
                'htmlLink': event.get('htmlLink')
            }

        except HttpError as error:
            logging.error(f'An error occurred: {error}')
            return None

    def update_event(self, event_id: str, summary: str = None, start_time: str = None,
                    end_time: str = None, description: str = None, location: str = None) -> Optional[Dict]:
        """Update an existing calendar event."""
        try:
            # First, get the existing event
            event = self.service.events().get(calendarId='primary', eventId=event_id).execute()

            # Update the fields that were provided
            if summary:
                event['summary'] = summary
            if start_time:
                event['start']['dateTime'] = start_time
                event['start']['timeZone'] = 'America/Bogota'  # Ensure timezone is set
                logging.warning(f'[UPDATE] Updating start time to: {start_time} with timezone America/Bogota')
            if end_time:
                event['end']['dateTime'] = end_time
                event['end']['timeZone'] = 'America/Bogota'  # Ensure timezone is set
                logging.warning(f'[UPDATE] Updating end time to: {end_time} with timezone America/Bogota')
            if description is not None:
                event['description'] = description
            if location is not None:
                event['location'] = location

            logging.warning(f'[UPDATE] Sending update request to Google Calendar for event {event_id}')
            logging.warning(f'[UPDATE] Request payload - Start: {event["start"]}, End: {event["end"]}')

            updated_event = self.service.events().update(
                calendarId='primary', eventId=event_id, body=event
            ).execute()

            logging.warning(f'[UPDATE] Google Calendar update response - Start: {updated_event["start"].get("dateTime")}, End: {updated_event["end"].get("dateTime")}')
            logging.warning(f'[UPDATE] Event updated successfully: {updated_event.get("htmlLink")}')

            return {
                'id': updated_event['id'],
                'summary': updated_event.get('summary', 'No title'),
                'start': updated_event['start'].get('dateTime'),
                'end': updated_event['end'].get('dateTime'),
                'description': updated_event.get('description', ''),
                'location': updated_event.get('location', ''),
                'htmlLink': updated_event.get('htmlLink')
            }

        except HttpError as error:
            logging.error(f'An error occurred: {error}')
            return None

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        try:
            self.service.events().delete(calendarId='primary', eventId=event_id).execute()
            logging.info(f'Event deleted: {event_id}')
            return True
        except HttpError as error:
            logging.error(f'An error occurred: {error}')
            return False

    def get_event(self, event_id: str) -> Optional[Dict]:
        """Get a specific calendar event."""
        try:
            event = self.service.events().get(calendarId='primary', eventId=event_id).execute()

            return {
                'id': event['id'],
                'summary': event.get('summary', 'No title'),
                'start': event['start'].get('dateTime'),
                'end': event['end'].get('dateTime'),
                'description': event.get('description', ''),
                'location': event.get('location', ''),
                'htmlLink': event.get('htmlLink')
            }

        except HttpError as error:
            logging.error(f'An error occurred: {error}')
            return None

# Global instance
calendar_service = GoogleCalendarService()