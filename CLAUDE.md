# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a WhatsApp bot for a Colombian barbería (barber shop) that integrates WhatsApp Business API, Google Calendar, and OpenAI/LangChain for appointment scheduling and customer service. The bot speaks Spanish with Colombian expressions and handles appointment booking through natural language conversations.

## Architecture

### Core Flow
```
WhatsApp User → Meta Cloud API → Flask Webhook → LangChain Service → Calendar Tools → Response
```

### Key Components
- **Flask App** (`app/__init__.py`): Factory pattern with webhook endpoints in `app/views.py`
- **LangChain Service** (`app/services/langchain_service.py`): Main AI orchestrator with conversation memory and tool calling
- **Calendar Integration** (`app/services/calendar_service.py` + `calendar_tools.py`): Google Calendar API with LangChain tools
- **WhatsApp Utils** (`app/utils/whatsapp_utils.py`): Message processing and API calls to Meta's WhatsApp Business API
- **Business Logic** (`app/services/barberia_info.py`): Services, pricing, and business information

### Message Processing Pipeline
1. Webhook receives WhatsApp message from Meta Cloud API
2. Security validation with HMAC signature verification (`app/decorators/security.py`)
3. Message processing through `process_whatsapp_message()` in WhatsApp utils
4. LangChain service generates response using GPT-4 with calendar tools
5. Response sent back through WhatsApp Business API

## Development Commands

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp example.env .env
# Edit .env with your API keys

# Set up Google Calendar OAuth
python setup_calendar_auth.py

# Run development server
python run.py
```

### Testing
The project has extensive test coverage with scenario-based testing:

```bash
# Test main assistant functionality
python test_barberia_assistant.py

# Test calendar integration
python test_calendar_tools.py

# Test complete appointment flows
python test_complete_appointment_flow.py

# Test specific features
python test_available_slots.py
python test_appointment_creation.py
python test_appointment_confirmation.py
python test_whatsapp_api.py

# Test confirmation formatting
python test_confirmation_format.py
```

### Ngrok for Webhook Testing
```bash
# Start ngrok with static domain (required for Meta webhook validation)
ngrok http 8000 --domain your-domain.ngrok-free.app
```

## Key Patterns & Conventions

### AI Service Architecture
- **Primary AI**: LangChain with GPT-4o-mini for main conversations
- **Tool Calling**: Calendar operations through LangChain tools (create, list, update, delete events)
- **Conversation Memory**: Persistent storage using Python `shelve`, keeps last 10 messages per user
- **Duplicate Prevention**: Built-in logic prevents multiple calendar events in same conversation
- **Timezone Handling**: Automatic Colombia timezone (UTC-5) conversion

### WhatsApp Integration
- **Webhooks**: GET for verification, POST for message processing
- **Message Validation**: Uses `is_valid_whatsapp_message()` for payload structure
- **Character Limits**: Handles 4096 char WhatsApp limit with message splitting
- **Error Recovery**: Fallback responses when AI services fail

### Calendar System
- **Capacity Management**: Maximum 2 simultaneous appointments to prevent overbooking
- **Natural Language**: Converts "mañana a las 3" to proper ISO datetime format
- **Overlap Detection**: Checks existing events before creating new ones
- **Business Hours**: Integrated with actual barbería schedule

### Colombian Business Context
- **Language**: Spanish with Colombian expressions ("parce", "¿Qué más pues?")
- **Currency**: Colombian Peso (COP) pricing
- **Services**: Corte ($15k-20k), Barba ($12k), Combos ($25k), Niños ($10k)
- **Payment Methods**: Cash, cards, Nequi, DaviPlata
- **Cultural Adaptation**: Regional communication style and business customs

## Environment Configuration

### Required Environment Variables
```env
# WhatsApp Business API (Meta)
ACCESS_TOKEN=""              # Meta permanent access token
APP_ID=""                   # Meta app ID
APP_SECRET=""               # Meta app secret
RECIPIENT_WAID=""           # Test phone number
PHONE_NUMBER_ID=""          # WhatsApp Business phone number ID
VERIFY_TOKEN=""             # Webhook verification token
VERSION="v18.0"             # Meta API version

# OpenAI
OPENAI_API_KEY=""           # OpenAI API key
OPENAI_ASSISTANT_ID=""      # Assistant ID (legacy support)
```

### Authentication Files
- `client_secret_*.json`: Google OAuth2 credentials (download from Google Cloud Console)
- `token.json`: Generated during `setup_calendar_auth.py` (don't commit)
- `conversation_history.db`: Auto-created for user conversations
- `threads_db`: Legacy OpenAI threads storage

## Development Notes

### Security Implementation
- All webhooks validated with `@signature_required` decorator
- HMAC SHA256 signature verification using Meta app secret
- Secrets managed through environment variables only
- Google OAuth2 with appropriate calendar scopes

### Testing Patterns
- **User Isolation**: Test users with unique IDs (`test_client_001`)
- **Scenario Testing**: Complete conversation flows from greeting to appointment confirmation
- **Error Simulation**: Network failures, API timeouts, invalid inputs
- **Integration Testing**: Real API calls to Google Calendar (uses test calendar)

### Calendar Tool Usage
When working with calendar functionality, understand these LangChain tools:
- `list_calendar_events`: View upcoming appointments
- `create_calendar_event`: Book appointments (includes overlap checking)
- `get_available_slots`: Check availability by time range (morning/afternoon/evening)
- `update_calendar_event`: Modify existing appointments
- `delete_calendar_event`: Cancel appointments by event ID
- `get_calendar_event`: Get specific appointment details

### Message Flow Debugging
- All operations logged with context (`[CALENDAR]`, `[TOOL]`, `[RESPONSE]` prefixes)
- Conversation history persisted per WhatsApp ID
- Tool execution results logged for debugging
- Error handling with user-friendly Spanish messages

### Colombian Date/Time Handling
- "mañana" = tomorrow, "hoy" = today
- Times like "a las 3" converted to 3:00 PM format
- All events stored in Colombia timezone (America/Bogota)
- Business operates Mon-Sat with varying hours, closed Sunday afternoons