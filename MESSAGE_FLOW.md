# WhatsApp Bot Message Flow

Complete explanation of how a WhatsApp message travels through your bot from start to finish.

---

## 📱 The Journey of a WhatsApp Message

### **Step 1: User Sends Message**
```
User in WhatsApp: "hola"
    ↓
WhatsApp sends message to Meta's servers
    ↓
Meta Cloud API processes message
```

### **Step 2: Meta Webhook Event**
Meta sends an HTTP POST request to your server:
```json
POST http://your-server.com:8000/webhook

{
  "entry": [{
    "changes": [{
      "value": {
        "metadata": {
          "phone_number_id": "717510114781982"  // ← Key for routing!
        },
        "contacts": [{
          "wa_id": "573177000722",              // ← User's WhatsApp ID
          "profile": { "name": "David" }
        }],
        "messages": [{
          "text": { "body": "hola" }            // ← The actual message
        }]
      }
    }]
  }]
}
```

---

## 🔄 Your Bot's Processing Flow

### **1. Flask App Startup** ([run.py](run.py))
```python
# run.py
app = create_app()  # Creates Flask application
app.run(host="0.0.0.0", port=8000)  # Starts server on port 8000
```

**What happens:**
- Imports `create_app()` from `app/__init__.py`
- Creates Flask application instance
- Starts web server listening on port 8000

---

### **2. App Initialization** ([app/__init__.py](app/__init__.py))
```python
# app/__init__.py
def create_app():
    app = Flask(__name__)

    load_configurations(app)      # Loads .env variables
    configure_logging()            # Sets up logging

    app.register_blueprint(webhook_blueprint)  # Registers /webhook routes

    return app
```

**What happens:**
- Creates Flask app
- Loads environment variables (ACCESS_TOKEN, DATABASE_URL, etc.)
- Configures logging
- Registers webhook routes (`/webhook` GET and POST)

---

### **3. Webhook Receives POST Request** ([app/views.py](app/views.py:84-87))
```python
# app/views.py
@webhook_blueprint.route("/webhook", methods=["POST"])
@signature_required  # ← Security: Verifies request is from Meta
def webhook_post():
    return handle_message()
```

**What happens:**
1. **Security Check** (`@signature_required`):
   - Validates HMAC SHA256 signature from Meta
   - Ensures request is authentic and not tampered with
   - Located in [app/decorators/security.py](app/decorators/security.py)

2. **Routes to** `handle_message()`

---

### **4. Message Handler** ([app/views.py](app/views.py:15-54))
```python
# app/views.py
def handle_message():
    body = request.get_json()  # Parse incoming JSON

    # Check if it's a status update (sent, delivered, read)
    if body.get("entry")[0].get("changes")[0].get("value").get("statuses"):
        return jsonify({"status": "ok"}), 200  # Ignore status updates

    # Validate it's a real message
    if is_valid_whatsapp_message(body):
        process_whatsapp_message(body)  # ← Process the message!
        return jsonify({"status": "ok"}), 200
```

**What happens:**
- Extracts JSON body from HTTP request
- Checks if it's a status update (ignores if yes)
- Validates message structure with `is_valid_whatsapp_message()`
- Routes to `process_whatsapp_message()`

---

### **5. Message Processing** ([app/utils/whatsapp_utils.py](app/utils/whatsapp_utils.py:135-174))
```python
# app/utils/whatsapp_utils.py
def process_whatsapp_message(body):
    # Extract phone_number_id for business routing
    phone_number_id = body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]

    # Lookup business by phone_number_id
    business_context = business_service.get_business_context(phone_number_id)

    # Extract user info
    wa_id = body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"]
    name = body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"]

    # Extract message text
    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    message_body = message["text"]["body"]

    # Generate AI response with business context
    response = langchain_service.generate_response(
        message_body, wa_id, name, business_context=business_context
    )

    # Format response for WhatsApp
    processed_response = process_text_for_whatsapp(response)

    # Send back to user
    data = get_text_message_input(wa_id, processed_response)
    send_message(data)
```

**What happens:**
1. **Extracts** `phone_number_id` from webhook (identifies which business)
2. **Looks up** business configuration from database
3. **Extracts** user's WhatsApp ID (`wa_id`) and name
4. **Extracts** message text
5. **Generates** AI response with business context (next step)
6. **Formats** response (removes markdown, handles bold/italic)
7. **Sends** response back to user via Meta API

---

### **6. AI Response Generation** ([app/services/langchain_service.py](app/services/langchain_service.py:85))
```python
# app/services/langchain_service.py
def generate_response(self, message_body: str, wa_id: str, name: str,
                     business_context=None) -> str:
    # Extract business_id from context
    business_id = business_context.get('business_id') if business_context else None

    # Set business context for booking tools (they'll read max_concurrent, etc.)
    set_business_context(business_context)

    # 1. Get conversation history from database (scoped to business)
    conversation_history = self.get_conversation_history(wa_id, business_id=business_id)

    # 2. Build system prompt dynamically from business configuration
    system_prompt = prompt_builder.build_system_prompt(
        business_context=business_context,
        current_date=f"{current_day}/{current_month}/{current_year}",
        current_year=current_year,
        wa_id=wa_id,
        name=name
    )
    # Prompt includes:
    # - Business name, location, personality (from database)
    # - Services and prices (from database)
    # - AI personality and tone (from database)
    # - max_concurrent setting (from database)
    # - Business hours (from database)
    # - Available booking tools

    # 3. Create message chain
    messages = [
        SystemMessage(content=system_prompt),
        ...conversation_history,
        HumanMessage(content=message_body)  # Current user message
    ]

    # 4. Call OpenAI GPT-4o-mini with booking tools
    response = self.llm_with_tools.invoke(messages)

    # 5. If AI wants to use booking tools (like schedule_appointment)
    if response.tool_calls:
        # Execute tools (e.g., create appointment record)
        # Tools automatically respect business-specific max_concurrent setting
        tool_results = []
        for tool_call in response.tool_calls:
            result = tool.invoke(tool_args)
            tool_results.append(result)

        # Generate final response based on tool results
        final_response = self.llm_with_tools.invoke([
            ...messages,
            HumanMessage(content=f"Tool results: {tool_results}")
        ])

    # 6. Store conversation in database (scoped to business)
    self.add_to_conversation_history(wa_id, "user", message_body, business_id)
    self.add_to_conversation_history(wa_id, "assistant", final_response, business_id)

    return final_response.content
```

**What happens:**
1. **Sets** business context globally for booking tools
2. **Retrieves** conversation history from PostgreSQL (filtered by business)
3. **Builds** system prompt dynamically from database:
   - Business personality and tone (editable by admin)
   - Services and prices (per business)
   - max_concurrent appointments (configurable per business)
   - Business hours and location
   - Available booking tools
4. **Sends** to OpenAI GPT-4o-mini
5. **If needed**, executes booking tools (respecting business-specific limits)
6. **Stores** conversation in database (linked to business_id)
7. **Returns** AI-generated response

---

### **7. Booking Tool Execution** ([app/services/calendar_tools.py](app/services/calendar_tools.py:179))

When user says "quiero agendar una cita mañana a las 10 AM":

```python
# app/services/calendar_tools.py
@tool
def schedule_appointment(whatsapp_id: str, summary: str,
                        start_time: str, end_time: str):
    # 1. Save customer info to database
    customer_service.create_or_update_customer(
        whatsapp_id=whatsapp_id,
        name=customer_name
    )

    # 2. Check for overlapping events (reads max_concurrent from business settings)
    max_concurrent = get_max_concurrent()  # Gets from business context (e.g., 2)
    has_overlap, event_count = check_overlapping_events(start_time, end_time)

    if has_overlap:
        return f"❌ Ya hay {event_count} citas en ese horario (máximo: {max_concurrent})"

    # 3. Create booking record in the in-house booking system
    booking = booking_service.create_booking(
        summary="Corte y barba",
        start_time="2025-10-12T10:00:00",
        end_time="2025-10-12T11:00:00",
        whatsapp_id=whatsapp_id
    )

    return "✅ Tu cita está agendada para el 12 de octubre a las 10:00 AM"
```

**What happens:**
1. **Saves** customer info to database
2. **Reads** `max_concurrent` from business settings (e.g., 2 for this business)
3. **Checks** calendar for availability (respects business-specific limit)
4. **Creates** appointment in the in-house booking system if available
5. **Returns** confirmation message to AI
6. **AI formats** confirmation for user

---

### **8. Database Operations** ([app/database/conversation_service.py](app/database/conversation_service.py))

```python
# app/database/conversation_service.py
def store_conversation_message(self, wa_id: str, message: str, role: str):
    # Create conversation record
    conversation = Conversation(
        business_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),  # Default business
        whatsapp_id=wa_id,
        message=message,
        role=role  # "user" or "assistant"
    )

    # Save to PostgreSQL
    session.add(conversation)
    session.commit()
```

**What happens:**
- Every message (user + assistant) is stored in PostgreSQL
- Linked to business_id (currently using default)
- Used for conversation memory (last 10 messages)

---

### **9. Response Formatting** ([app/utils/whatsapp_utils.py](app/utils/whatsapp_utils.py:107))

```python
# app/utils/whatsapp_utils.py
def process_text_for_whatsapp(text: str) -> str:
    # Convert markdown to WhatsApp format
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'_*\1*_', text)  # Bold+Italic
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)        # Bold
    text = re.sub(r'\_\_(.+?)\_\_', r'_\1_', text)        # Italic
    text = text.replace('**', '')  # Clean remaining **

    return text
```

**What happens:**
- Converts markdown formatting to WhatsApp's format
- **Bold**: `*text*`
- _Italic_: `_text_`
- Preserves emojis

---

### **10. Send Response** ([app/utils/whatsapp_utils.py](app/utils/whatsapp_utils.py:42))

```python
# app/utils/whatsapp_utils.py
def send_message(data):
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"

    response = requests.post(url, data=data, headers=headers)
    return response
```

**What happens:**
1. **Constructs** HTTP POST request to Meta's API
2. **Uses** access token from environment
3. **Sends** to WhatsApp Cloud API
4. **User receives** message in their WhatsApp

---

## 🔄 Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. USER SENDS MESSAGE                                       │
│    WhatsApp: "hola"                                         │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. META CLOUD API                                           │
│    Webhook POST → http://your-server:8000/webhook          │
│    {"contacts": [{"wa_id": "573177000722"}], ...}         │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. FLASK SERVER (run.py → app/__init__.py)                │
│    ✓ Load config from .env                                 │
│    ✓ Setup logging                                         │
│    ✓ Register /webhook routes                              │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. WEBHOOK HANDLER (app/views.py)                          │
│    @signature_required → Verify Meta signature             │
│    handle_message() → Parse JSON body                      │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. MESSAGE PROCESSOR (app/utils/whatsapp_utils.py)        │
│    Extract: wa_id, name, message_body                      │
│    Call: langchain_service.generate_response()             │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. AI SERVICE (app/services/langchain_service.py)         │
│    ✓ Load conversation history (PostgreSQL)                │
│    ✓ Build system prompt (barbería personality)            │
│    ✓ Call OpenAI GPT-4o-mini                              │
│    ✓ Execute booking tools if needed                       │
│    ✓ Save conversation to DB                               │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. BOOKING TOOLS (app/services/calendar_tools.py)         │
│    If appointment requested:                                │
│    ✓ Save customer to DB                                   │
│    ✓ Check booking availability                            │
│    ✓ Create appointment                                    │
│    Return: "✅ Cita agendada..."                           │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. DATABASE (PostgreSQL/Supabase)                          │
│    ✓ Store conversation (user + assistant messages)        │
│    ✓ Store customer info                                   │
│    ✓ Link to business_id                                   │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 9. FORMAT RESPONSE (app/utils/whatsapp_utils.py)          │
│    Convert markdown → WhatsApp format                       │
│    Build message JSON payload                               │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 10. SEND TO META API                                        │
│     POST https://graph.facebook.com/v22.0/.../messages     │
│     Authorization: Bearer {ACCESS_TOKEN}                    │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│ 11. USER RECEIVES MESSAGE                                   │
│     WhatsApp: "¡Hola, David! 🙌 ¿Todo bien?..."           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 Key Components

### **1. Environment Variables (.env)**
```env
ACCESS_TOKEN=""           # Meta access token (to send messages)
PHONE_NUMBER_ID=""        # Your WhatsApp Business phone number ID
VERIFY_TOKEN=""           # Webhook verification token
OPENAI_API_KEY=""         # OpenAI API key
DATABASE_URL=""           # PostgreSQL connection string
```

### **2. Database Tables (PostgreSQL)**
- **businesses** - Business configurations
- **whatsapp_numbers** - WhatsApp numbers linked to businesses
- **customers** - Customer information
- **conversations** - Message history (user + assistant)

### **3. External APIs**
- **Meta WhatsApp Cloud API** - Receive/send messages
- **OpenAI GPT-4o-mini** - Generate responses
- **In-house booking APIs** - Schedule appointments

---

## ✅ Multi-Tenant Architecture (IMPLEMENTED)

### **How It Works Now**
```
Incoming Message
  ↓
Extract phone_number_id from webhook: "717510114781982"
  ↓
Lookup business: business_service.get_business_context(phone_number_id)
  ↓
Load business-specific configuration from database:
  - Business name, location, type
  - Services and prices
  - AI personality and tone
  - max_concurrent appointments (e.g., 2)
  - Business hours
  - Staff information
  ↓
Generate business-specific response using:
  - Dynamic prompts from database
  - Business-specific booking limits
  - Conversation history filtered by business
  ↓
Response sent with ACCESS_TOKEN from .env
```

### **Key Features**

✅ **Business Routing** - Each WhatsApp number maps to a business via `phone_number_id`
✅ **Dynamic Configuration** - All business settings stored in PostgreSQL
✅ **Custom AI Prompts** - Editable by super admins without code deployment
✅ **Per-Business Limits** - Each business can set their own `max_concurrent` appointments
✅ **Conversation Isolation** - Conversations are scoped to businesses
✅ **Scalable** - Add new businesses by inserting records in database

### **Database Schema**

- **businesses** - Store business configuration (name, type, settings)
- **whatsapp_numbers** - Map phone_number_id to business_id
- **conversations** - Message history (linked to business_id)
- **customers** - Customer info (business-agnostic)
- **users** - System users who can manage businesses
- **user_businesses** - User-business access control

---

## 📝 Example Message Processing

**Input:** User sends "quiero agendar corte mañana a las 10"

**Processing:**
1. ✅ Webhook receives POST from Meta
2. ✅ Extracts wa_id: "573177000722", message: "quiero agendar corte mañana a las 10"
3. ✅ Loads conversation history (last 10 messages)
4. ✅ Sends to GPT-4o-mini with system prompt (barbería personality)
5. ✅ AI decides to use `schedule_appointment` tool
6. ✅ Tool creates appointment record
7. ✅ AI generates: "✅ Tu cita está agendada para el 12 de octubre a las 10:00 AM, David!"
8. ✅ Formats for WhatsApp
9. ✅ Sends to user

**Output:** User receives confirmation in WhatsApp

---

## 🐛 Debugging Tips

**See what's happening:**
```bash
# Watch logs in real-time
tail -f flask.log

# Look for these patterns:
# - "Processing message from..."
# - "[TOOL] Tool calls detected..."
# - "[BOOKING] Appointment created..."
# - "Message sent successfully..."
```

**Common issues:**
- **401 Unauthorized** → ACCESS_TOKEN expired
- **business_id null** → Database migration not run (now fixed!)
- **Empty response** → OpenAI API error, check OPENAI_API_KEY
- **Tool not executing** → Check tool name matches in system prompt

---

This is the complete flow from start to finish! 🚀
