# Debugging Guide: Messages Arrive But Bot Doesn't Respond

## Enhanced Logging Added

I've added comprehensive debug logging throughout the message processing pipeline. When you run the app, you'll now see detailed logs at each step.

## How to Debug

### 1. Start the App with Debug Mode

```bash
# Make sure FLASK_DEBUG is set in .env
FLASK_DEBUG=True

# Or set it when running
FLASK_DEBUG=True python3 run.py
```

### 2. Watch the Logs

When a message arrives, you should see logs like:

```
[DEBUG] ========== INCOMING WEBHOOK ==========
[DEBUG] Full request body: {...}
[DEBUG] Valid WhatsApp message detected
[ROUTING] Extracted phone_number_id: ...
[DEBUG] ========== PROCESSING MESSAGE ==========
[MESSAGE] Processing message from ...
[DEBUG] Calling LangChain service...
[DEBUG] Sending message to WhatsApp API...
✅ Message sent successfully to WhatsApp API
```

### 3. Common Issues to Check

#### Issue 1: No phone_number_id in webhook
**Symptom:** Logs show `[ROUTING] ❌ No phone_number_id in webhook`
**Solution:** Check the webhook payload structure. The phone_number_id might be in a different location.

#### Issue 2: Business context not found
**Symptom:** Logs show `[ROUTING] ❌ No business found for phone_number_id`
**Solution:** 
- Verify the phone_number_id exists in the database
- Check that the whatsapp_numbers table has the correct phone_number_id
- Ensure the business is active

#### Issue 3: LangChain service returns empty
**Symptom:** Logs show `❌ LangChain service returned None or empty response`
**Solution:**
- Check OPENAI_API_KEY is set correctly
- Verify the API key is valid and has credits
- Check for errors in the LangChain service logs

#### Issue 4: Message sending fails
**Symptom:** Logs show `❌ Failed to send message to WhatsApp API`
**Solution:**
- Check ACCESS_TOKEN is valid
- Verify PHONE_NUMBER_ID is correct
- Check API response for error details (logged in debug output)

#### Issue 5: Errors in processing
**Symptom:** Logs show `❌ Error processing WhatsApp message`
**Solution:**
- Check the full traceback in logs
- Verify database connection (DATABASE_URL)
- Check all required environment variables are set

## Quick Debug Checklist

- [ ] FLASK_DEBUG=True in .env
- [ ] ACCESS_TOKEN is valid and not expired
- [ ] PHONE_NUMBER_ID matches your Meta Business Manager
- [ ] OPENAI_API_KEY is set and valid
- [ ] DATABASE_URL is correct and database is accessible
- [ ] phone_number_id exists in whatsapp_numbers table
- [ ] Business is active in database
- [ ] Webhook is properly configured in Meta Dashboard

## Testing Steps

1. Send a test message to your WhatsApp number
2. Watch the console logs for the debug output
3. Look for any ❌ error markers
4. Check the traceback for specific error details
5. Verify each step completes successfully

## Viewing Logs in Production

If running on Railway or similar:
```bash
railway logs
# or
heroku logs --tail
```

The enhanced logging will help identify exactly where the process is failing.
