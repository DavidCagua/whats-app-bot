#!/usr/bin/env python3
"""
Setup script for Google Calendar authentication.
This script will help you authenticate with the Google Calendar API.
"""

import os
import logging
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']

def setup_calendar_auth():
    """Setup Google Calendar authentication."""
    creds = None

    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'client_secret_873374688567-icspa759as15biuvdta695qv2mv7ldbp.apps.googleusercontent.com.json', SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)

        # Test the connection by listing calendars
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get('items', [])

        print("✅ Successfully authenticated with Google Calendar API!")
        print(f"Found {len(calendars)} calendar(s):")
        for calendar in calendars:
            print(f"  - {calendar['summary']} ({calendar['id']})")

        return True

    except HttpError as error:
        print(f"❌ An error occurred: {error}")
        return False

if __name__ == '__main__':
    print("🔧 Setting up Google Calendar authentication...")
    print("This will open a browser window for authentication.")
    print("Please follow the authentication flow in your browser.")
    print()

    success = setup_calendar_auth()

    if success:
        print()
        print("🎉 Setup completed successfully!")
        print("You can now run your WhatsApp bot with calendar functionality.")
    else:
        print()
        print("❌ Setup failed. Please check your credentials and try again.")