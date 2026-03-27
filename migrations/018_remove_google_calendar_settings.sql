-- Remove legacy Google Calendar OAuth settings from businesses JSON config.
UPDATE businesses
SET settings = settings - 'google_calendar'
WHERE settings ? 'google_calendar';
