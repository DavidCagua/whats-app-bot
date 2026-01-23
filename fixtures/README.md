# Webhook Fixtures

Sample webhook payloads for local testing in mock mode.

## Usage

Use these fixtures with the `replay_fixture.py` script to test the full webhook → agent → tools flow locally without Meta API access.

## Fixture Files

- `simple_greeting.json` - Simple greeting message
- `appointment_request.json` - Request to schedule an appointment
- `check_availability.json` - Check available time slots

## Creating New Fixtures

When creating new fixtures, ensure they follow the Meta WhatsApp webhook payload structure:

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {
          "display_phone_number": "15550555555",
          "phone_number_id": "123456789"
        },
        "contacts": [{
          "profile": {
            "name": "User Name"
          },
          "wa_id": "573001234567"
        }],
        "messages": [{
          "from": "573001234567",
          "id": "wamid.unique_message_id",
          "timestamp": "1234567890",
          "text": {
            "body": "Message text here"
          },
          "type": "text"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

## Required Fields

- `entry[0].changes[0].value.metadata.phone_number_id` - Used for business routing
- `entry[0].changes[0].value.contacts[0].wa_id` - User's WhatsApp ID
- `entry[0].changes[0].value.messages[0].id` - Unique message ID (for deduplication)
- `entry[0].changes[0].value.messages[0].text.body` - Message content
