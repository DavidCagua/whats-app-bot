# WhatsApp Bot Test Suite - Simplified Architecture

This test suite validates the simplified calendar tool architecture for the WhatsApp barbería bot.

## Architecture Changes

The test suite has been completely rewritten to test the new simplified architecture:

### Old Complex Architecture (Removed)
- ❌ Multi-step workflows requiring LLM to chain tools
- ❌ Complex ID management and event finding
- ❌ Separate `create_calendar_event`, `update_calendar_event`, `delete_calendar_event`, `find_user_appointments` tools
- ❌ Error-prone ID passing between tools

### New Simplified Architecture (Current)
- ✅ Single atomic operations that handle complexity internally
- ✅ WhatsApp ID-based appointment management
- ✅ Three main tools: `schedule_appointment`, `reschedule_appointment`, `cancel_appointment`
- ✅ Automatic appointment finding and updating without exposing IDs to LLM

## Test Files

### 1. `test_basic_functionality.py`
**Quick smoke tests to verify core functionality**
```bash
python tests/test_basic_functionality.py
```
- Tests module imports
- Validates tool availability and configuration
- Checks LangChain service initialization
- Verifies environment variables

### 2. `test_simplified_calendar_tools.py`
**Tests the new simplified calendar tools**
```bash
python tests/test_simplified_calendar_tools.py
```
- `schedule_appointment`: Book new appointments
- `reschedule_appointment`: Move existing appointments to new times
- `cancel_appointment`: Cancel existing appointments
- `get_available_slots`: Show available time slots
- Complete appointment flow testing

### 3. `test_whatsapp_integration.py`
**Tests complete WhatsApp conversation flows**
```bash
python tests/test_whatsapp_integration.py
```
- Appointment scheduling conversations
- Appointment rescheduling conversations
- Appointment cancellation conversations
- Availability inquiries
- General business inquiries

### 4. `run_all_tests.py`
**Comprehensive test runner**
```bash
python tests/run_all_tests.py
```
- Runs all test suites in sequence
- Provides detailed reporting
- Shows comprehensive summary

## Running Tests

### Quick Start
```bash
# Run basic functionality tests first
python tests/test_basic_functionality.py

# Run all tests
python tests/run_all_tests.py
```

### Individual Test Suites
```bash
# Test calendar tools only
python tests/test_simplified_calendar_tools.py

# Test WhatsApp integration only
python tests/test_whatsapp_integration.py
```

## Test User Isolation

All tests use isolated test WhatsApp IDs to prevent interference:
- `test_user_simplified_001` - Calendar tools tests
- `test_whatsapp_user_001` - Integration tests

## Expected Behavior

### Successful Tool Responses
- **schedule_appointment**: "Tu cita 'X' ha sido agendada exitosamente para el [date] a las [time], parce! 📅"
- **reschedule_appointment**: "Tu cita 'X' ha sido reagendada exitosamente para el [date] a las [time], parce! 📅"
- **cancel_appointment**: "Tu cita 'X' ha sido cancelada exitosamente, parce. Si necesitas reagendar, aquí estoy para ayudarte! 📅"

### Error Handling
- No appointments found: "No se encontraron citas para [operation]"
- Invalid datetime format: "Formato de fecha/hora inválido"
- Calendar conflicts: Automatic handling with overlap checking

## Key Improvements

1. **Simplified User Experience**: Users never need to provide or know event IDs
2. **Automatic Appointment Management**: Tools automatically find user appointments by WhatsApp ID
3. **Reduced Error Potential**: Single atomic operations prevent multi-step failures
4. **Better Colombian Spanish**: Responses use natural Colombian expressions
5. **Comprehensive Error Handling**: Clear error messages in Spanish

## Environment Requirements

Ensure these environment variables are set:
```env
OPENAI_API_KEY=your_openai_key
ACCESS_TOKEN=your_whatsapp_access_token
PHONE_NUMBER_ID=your_phone_number_id
VERIFY_TOKEN=your_verify_token
```

## Calendar Setup

Tests require Google Calendar API access:
1. Run `python setup_calendar_auth.py` to authenticate
2. Ensure `token.json` is generated
3. Tests use the authenticated calendar

## Colombian Context

Tests validate Colombian-specific features:
- Spanish responses with Colombian expressions
- Colombian Peso (COP) pricing
- America/Bogota timezone handling
- Regional business customs and communication style