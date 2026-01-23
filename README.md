# WhatsApp AI Agent

A production-ready WhatsApp bot built on Meta's Cloud API that provides intelligent conversational AI with calendar integration and multi-tenant business support.

## What It Does

- **WhatsApp Cloud API Webhook Bot**: Receives and processes incoming messages via Meta's webhook infrastructure with signature verification and multi-tenant routing based on phone number IDs
- **LangChain Tool Calling**: Uses OpenAI GPT-4o-mini with LangChain's tool binding to execute calendar operations (list, create, update, delete events) based on natural language requests
- **Google Calendar Actions**: Integrates with Google Calendar API to manage appointments, check availability, and enforce business-specific concurrency limits (e.g., max simultaneous appointments)
- **Dynamic Role/System Prompts**: Generates AI system prompts dynamically from database configuration, allowing admins to customize business personality, services, pricing, and behavior without code deployments
- **Conversational Message Flow**: Maintains conversation history in PostgreSQL, processes messages through a multi-iteration agent loop, and formats responses for WhatsApp's text formatting (bold, italic, character limits)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Webhook Reception                                        │
│    Meta Cloud API → POST /webhook                          │
│    Signature verification (HMAC SHA256)                    │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Router & Business Context                                │
│    Extract phone_number_id from webhook                     │
│    Lookup business configuration from database             │
│    Load business-specific settings (prompts, limits, etc.)  │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. AI Agent (LangChain Service)                            │
│    Retrieve conversation history (last 10 messages)         │
│    Build dynamic system prompt from business config         │
│    Invoke GPT-4o-mini with calendar tools bound           │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Tool Execution (if needed)                              │
│    Execute calendar tools (schedule_appointment, etc.)     │
│    Respect business-specific max_concurrent limits          │
│    Create/update Google Calendar events                    │
│    Return tool results to agent for final response          │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Response & Storage                                       │
│    Format response (markdown → WhatsApp format)             │
│    Send via Meta WhatsApp API                               │
│    Store conversation in PostgreSQL (scoped to business)   │
└─────────────────────────────────────────────────────────────┘
```

**Key Components:**
- **Flask Application** (`app/`): Webhook handlers, message processing, business routing
- **LangChain Service** (`app/services/langchain_service.py`): AI agent with tool calling, conversation management
- **Calendar Tools** (`app/services/calendar_tools.py`): Google Calendar operations as LangChain tools
- **Database Services** (`app/database/`): Business configuration, conversation history, customer management
- **Admin Console** (`admin-console/`): Next.js dashboard for managing businesses, users, and AI prompts

## Operational Notes

### Idempotency
- **Webhook Message Deduplication**: Implemented message ID tracking to prevent processing the same WhatsApp message twice. Uses database storage (PostgreSQL) for persistent deduplication in production, with automatic fallback to in-memory LRU cache with 24-hour TTL for local development. Duplicate webhooks return 200 OK immediately without re-processing.
- **Calendar Events**: Basic duplicate prevention checks for recent appointment creation (within 5 minutes) to avoid duplicate calendar events in the same conversation
- **Database Writes**: Conversation history and customer records use upsert patterns where applicable
- **Implementation**: Message IDs are extracted from Meta webhook payload (`messages[0].id`) and stored in `processed_messages` table (or memory cache). The deduplication check happens before message processing in the webhook handler (`app/views.py`).

### Retries
- **WhatsApp API Calls**: HTTP requests to Meta API include timeout handling (10 seconds) but no automatic retry logic. Failed sends are logged but not retried
- **OpenAI API**: LangChain's ChatOpenAI client uses default retry behavior from the OpenAI SDK
- **Google Calendar API**: Uses google-api-python-client with default retry mechanisms
- **TODO**: Consider implementing exponential backoff retry for critical WhatsApp message sends

### Logging
- **Comprehensive Debug Logging**: All message processing steps log at WARNING/INFO level with structured prefixes (`[DEBUG]`, `[ROUTING]`, `[TOOL]`, `[BUSINESS]`, etc.)
- **Error Tracking**: Full tracebacks logged for exceptions, including webhook payloads for debugging
- **Status Updates**: WhatsApp message status webhooks (sent, delivered, read, failed) are logged with error details
- **Log Levels**: Uses Python's `logging` module; configure via Flask app configuration

### Rate Limits
- **Meta WhatsApp API**: No explicit rate limit handling implemented. Meta enforces rate limits per phone number; monitor for 429 responses
- **OpenAI API**: Relies on OpenAI SDK's built-in rate limit handling
- **Google Calendar API**: No explicit rate limit handling; Google enforces quotas per project
- **TODO**: Implement rate limit monitoring and backoff strategies for production scale

## Local Setup

### Prerequisites
- Python 3.8+
- PostgreSQL database (or Supabase)
- Meta Developer Account with WhatsApp Business API access
- Google Cloud Project with Calendar API enabled
- OpenAI API key

### Installation

1. **Clone and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Set up environment variables:**
   Create a `.env` file based on `example.env`:

   | Variable | Description | Required |
   |----------|-------------|----------|
   | `ACCESS_TOKEN` | Meta WhatsApp API access token (system user token recommended for long-lived) | Yes |
   | `PHONE_NUMBER_ID` | Default WhatsApp Business phone number ID | Yes |
   | `APP_ID` | Meta App ID | Yes |
   | `APP_SECRET` | Meta App Secret (for webhook signature verification) | Yes |
   | `VERIFY_TOKEN` | Custom token for webhook verification | Yes |
   | `VERSION` | Meta Graph API version (e.g., `v18.0`) | Yes |
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o-mini | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `FLASK_DEBUG` | Set to `"True"` for development | No |
| `TRACER_TYPE` | Tracer type: `"console"` (default) or `"langfuse"` | No |
| `TRACE_LOG_PII` | Set to `"true"` to log raw phone numbers and messages (DEBUG only) | No |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (required if using Langfuse tracer) | No |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (required if using Langfuse tracer) | No |
| `LANGFUSE_HOST` | Langfuse host URL (default: `https://cloud.langfuse.com`) | No |
| `MOCK_MODE` | Set to `"true"` to enable local testing without Meta API access | No |

3. **Database setup:**
   ```bash
   # Run migrations (see migrations/README.md)
   python run_migration.py
   ```

4. **Google Calendar setup:**
   ```bash
   # Follow CALENDAR_INTEGRATION.md for OAuth setup
   python setup_calendar_auth.py
   ```

5. **Run the application:**
   ```bash
   python run.py
   ```
   Server runs on `http://127.0.0.1:8000`

6. **Configure webhook (Production):**
   - Use ngrok or similar to expose localhost: `ngrok http 8000 --domain your-domain.ngrok-free.app`
   - In Meta App Dashboard → WhatsApp → Configuration:
     - Callback URL: `https://your-domain.ngrok-free.app/webhook`
     - Verify Token: Match your `VERIFY_TOKEN` from `.env`
     - Subscribe to `messages` field

### Local Testing (Mock Mode)

You can test the full webhook → agent → tools flow locally without Meta API access:

1. **Enable mock mode:**
   ```bash
   export MOCK_MODE=true
   # Or add to .env file:
   # MOCK_MODE=true
   ```

2. **Start the server:**
   ```bash
   python run.py
   ```

3. **Replay fixtures:**
   ```bash
   # Test a single fixture
   python replay_fixture.py fixtures/simple_greeting.json
   
   # Test all fixtures
   python replay_fixture.py fixtures/*.json
   
   # Custom server URL
   SERVER_URL=http://localhost:8000 python replay_fixture.py fixtures/appointment_request.json
   ```

**What mock mode does:**
- ✅ Skips signature verification (no Meta credentials needed)
- ✅ Mocks WhatsApp API calls (logs messages instead of sending)
- ✅ Full agent flow works (LLM, tools, tracing)
- ✅ Tests webhook routing, deduplication, and business context

**Mock mode output:**
- Server logs show full execution flow
- Messages are logged with `[MOCK MODE]` prefix instead of being sent
- Tracing information is available
- Tool calls execute normally (calendar tools may need Google credentials)

See `fixtures/README.md` for creating custom fixtures.

### Admin Console Setup

The admin console is a separate Next.js application for managing businesses and configurations:

```bash
cd admin-console
npm install
npm run dev
```

See `admin-console/README.md` for detailed setup instructions.

## Tracing

The application includes a lightweight tracing abstraction for monitoring agent runs. Tracing is enabled by default with console output.

### Console Tracer (Default)

The console tracer logs agent execution to standard logging with structured output:
- Run IDs for tracking individual agent executions
- Hashed user IDs (PII-safe by default)
- Tool call tracking with latency
- Error logging
- Total execution latency

Example trace output:
```
[TRACE] Run started: abc123 | user=a1b2c3d4 | message_id=wamid.xyz
[TRACE] LLM call: iteration=1 | has_tool_calls=True
[TRACE] Tool call: schedule_appointment | args={...}
[TRACE] Tool result: schedule_appointment | success=True
[TRACE] Run ended: abc123 | success=True | latency=1234.56ms | tools=1 | errors=0
```

### Langfuse Tracer (Optional)

To enable Langfuse tracing for production observability:

1. **Install Langfuse** (optional, only needed if using):
   ```bash
   pip install langfuse
   ```

2. **Set environment variables**:
   ```env
   TRACER_TYPE=langfuse
   LANGFUSE_SECRET_KEY=sk-...
   LANGFUSE_PUBLIC_KEY=pk-...
   LANGFUSE_HOST=https://cloud.langfuse.com  # Optional, defaults to cloud
   ```

3. **Note**: The Langfuse tracer is a skeleton implementation. Full integration requires completing the TODO sections in `app/services/tracing.py`.

### PII Protection

By default, tracing does not log:
- Raw phone numbers (hashed with SHA256)
- Raw message text (only logged if `TRACE_LOG_PII=true`)
- Customer names (redacted in tool arguments)

To enable PII logging for debugging (not recommended for production):
```env
TRACE_LOG_PII=true
```

### Traced Fields

Each agent run traces:
- `run_id`: Unique identifier for the run
- `user_id`: Hashed phone number/WhatsApp ID
- `message_id`: WhatsApp message ID from webhook
- `business_id`: Business identifier (if available)
- `tool_calls`: List of tool invocations with arguments (sanitized)
- `errors`: Any errors encountered during execution
- `latency_ms`: Total execution time in milliseconds

## Production Deployment

- **Web Server**: Use Gunicorn (included in requirements.txt) for production
- **Database**: PostgreSQL (Supabase recommended for managed hosting)
- **Environment**: Set `FLASK_DEBUG="False"` in production
- **Webhook URL**: Use a production domain with valid SSL certificate
- **Tracing**: Use console tracer for logs, or configure Langfuse for advanced observability
- **Monitoring**: TODO - Set up application monitoring and alerting for webhook failures, API errors, and database issues

## Documentation

- [MESSAGE_FLOW.md](MESSAGE_FLOW.md) - Detailed message processing flow
- [CALENDAR_INTEGRATION.md](CALENDAR_INTEGRATION.md) - Google Calendar setup guide
- [ROLE_SYSTEM.md](ROLE_SYSTEM.md) - Multi-tenant role and permission system
- [DATABASE_MIGRATIONS.md](DATABASE_MIGRATIONS.md) - Database schema and migrations

## License

See [LICENCE.txt](LICENCE.txt)
