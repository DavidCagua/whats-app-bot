# WhatsApp Bot with Google Calendar Integration

This WhatsApp bot now includes Google Calendar integration using LangChain tool calling capabilities. Users can manage their calendar events directly through WhatsApp messages.

## Features

- **List Events**: View upcoming calendar events
- **Create Events**: Add new events to your calendar
- **Update Events**: Modify existing events
- **Delete Events**: Remove events from your calendar
- **Get Event Details**: Retrieve specific event information

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Google Calendar API Setup

1. **Enable Google Calendar API**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one
   - Enable the Google Calendar API
   - Create credentials (OAuth 2.0 Client ID)
   - Download the credentials JSON file

2. **Place the credentials file**:
   - Save your credentials file as `client_secret_873374688567-icspa759as15biuvdta695qv2mv7ldbp.apps.googleusercontent.com.json` in the project root

3. **Run the authentication setup**:
   ```bash
   python setup_calendar_auth.py
   ```
   - This will open a browser window for authentication
   - Follow the OAuth flow to grant calendar access
   - A `token.json` file will be created for future use

### 3. Environment Variables

Make sure your `.env` file includes:

```env
OPENAI_API_KEY=your_openai_api_key
VERIFY_TOKEN=your_whatsapp_verify_token
ACCESS_TOKEN=your_whatsapp_access_token
PHONE_NUMBER_ID=your_whatsapp_phone_number_id
VERSION=your_whatsapp_api_version
```

### 4. Test the Integration

Run the test script to verify everything works:

```bash
python test_calendar_tools.py
```

## Usage Examples

Users can interact with the bot using natural language:

### List Events
- "Show me my upcoming events"
- "What's on my calendar today?"
- "List my events for this week"

### Create Events
- "Create a meeting tomorrow at 2 PM for 1 hour"
- "Add an event called 'Team Lunch' on Friday at 12 PM"
- "Schedule a call with John on Monday at 10 AM"

### Update Events
- "Change my meeting tomorrow to 3 PM"
- "Update the team lunch to 1 PM"
- "Move my call to Tuesday at 2 PM"

### Delete Events
- "Cancel my meeting tomorrow"
- "Delete the team lunch event"
- "Remove the call with John"

## Architecture

### Multi-Tenant Calendar Integration

The system supports per-business Google Calendar credentials, allowing each business to connect their own Google Calendar.

#### How It Works

1. **Business Admin connects calendar**: In Admin Console > Business Settings, click "Connect Google Calendar"
2. **OAuth flow**: Admin authenticates with their Google account
3. **Credentials stored**: OAuth tokens are encrypted and stored in the business's settings
4. **WhatsApp bot uses business calendar**: When users interact with the bot, it uses the connected business's calendar

#### Admin Console Environment Variables

Add these to your `admin-console/.env.local`:

```env
# Google OAuth for Calendar Integration (Admin Console)
GOOGLE_OAUTH_CLIENT_ID=your-oauth-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-oauth-client-secret
```

**Note**: These are the OAuth 2.0 credentials for the admin console to authenticate business admins. Each business's refresh token is then stored encrypted in the database.

### Components

1. **GoogleCalendarService** (`app/services/calendar_service.py`):
   - Handles authentication with Google Calendar API
   - Provides methods for CRUD operations on events
   - Factory methods for per-business credentials:
     - `from_business_credentials()`: Create service from explicit credentials
     - `from_business_context()`: Create service from business context dict

2. **Calendar Tools** (`app/services/calendar_tools.py`):
   - LangChain tools for calendar operations
   - Wraps the calendar service for LLM integration
   - Uses `get_calendar_service()` helper to get per-business service

3. **LangChain Service** (`app/services/langchain_service.py`):
   - Integrates OpenAI with calendar tools
   - Handles tool calling and response generation

4. **WhatsApp Utils** (`app/utils/whatsapp_utils.py`):
   - Updated to use the new LangChain service
   - Processes messages and sends responses

5. **Calendar Actions** (`admin-console/lib/actions/calendar.ts`):
   - Server actions for OAuth flow
   - `getCalendarStatus()`: Check if calendar is connected
   - `disconnectGoogleCalendar()`: Remove calendar connection
   - `saveGoogleCalendarCredentials()`: Store OAuth tokens

6. **API Routes** (`admin-console/app/api/calendar/`):
   - `/connect`: Initiates OAuth flow
   - `/callback`: Handles OAuth callback and saves credentials

### Tool Calling Flow

1. User sends a WhatsApp message
2. Message is processed by `process_whatsapp_message()`
3. LangChain service generates response with potential tool calls
4. If tools are called, they execute calendar operations
5. Results are incorporated into the final response
6. Response is sent back to the user via WhatsApp

## Error Handling

The integration includes comprehensive error handling:

- **Authentication errors**: Clear messages about setup requirements
- **API errors**: Graceful handling of Google Calendar API issues
- **Invalid inputs**: Validation of datetime formats and event data
- **Network issues**: Timeout and connection error handling

## Security Considerations

### Single-Tenant Mode (Environment Variables)
- OAuth tokens are stored locally in `token.json`
- API keys are stored in environment variables
- Calendar access is limited to the authenticated user's primary calendar

### Multi-Tenant Mode (Per-Business)
- OAuth refresh tokens are encrypted using AES-256-GCM before storage
- Encryption key derived from `ENCRYPTION_SECRET` or `NEXTAUTH_SECRET`
- Each business's credentials are isolated in their settings JSON
- OAuth Client ID/Secret stored on server, not in database
- Business admins can only connect/disconnect their own calendars

### General
- All operations are logged for debugging purposes
- API routes verify authentication and business permissions

## Troubleshooting

### Common Issues

1. **Authentication Failed**:
   - Run `python setup_calendar_auth.py` again
   - Check that your credentials file is correctly named
   - Ensure Google Calendar API is enabled

2. **Tool Calling Not Working**:
   - Verify OpenAI API key is valid
   - Check that all dependencies are installed
   - Review logs for specific error messages

3. **Events Not Appearing**:
   - Check that you're looking at the correct calendar
   - Verify timezone settings
   - Ensure events are being created with valid datetime formats

### Debug Mode

Enable detailed logging by setting the log level:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Future Enhancements

- **Multiple Calendars**: Support for multiple calendar accounts
- **Recurring Events**: Handle recurring event creation and management
- **Event Templates**: Predefined event templates for common scenarios
- **Calendar Sharing**: Share calendar events with other users
- **Reminders**: Set up event reminders and notifications
- **Conflict Detection**: Warn about scheduling conflicts

## API Reference

### Calendar Tools

- `list_calendar_events(max_results: int)`: List upcoming events
- `create_calendar_event(summary, start_time, end_time, description, location)`: Create new event
- `update_calendar_event(event_id, **kwargs)`: Update existing event
- `delete_calendar_event(event_id)`: Delete event
- `get_calendar_event(event_id)`: Get event details

### Datetime Format

All datetime values should be in ISO format with UTC timezone:
- Example: `"2024-01-15T10:00:00Z"`
- Use `Z` suffix to indicate UTC timezone

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review the logs for error messages
3. Test individual components using the test script
4. Verify your Google Calendar API setup