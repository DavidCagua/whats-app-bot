-- Migration 019: Repair bookings.service_id (idempotent)
--
-- Some databases had migration 016 partially applied: `services` exists but
-- `bookings.service_id` was never added. This migration is safe to run on any
-- environment: it only adds the column and index when missing.

-- ============================================================================
-- bookings.service_id + FK + index (matches 016 section 2)
-- ============================================================================
ALTER TABLE public.bookings
  ADD COLUMN IF NOT EXISTS service_id UUID REFERENCES public.services(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_bookings_service_id ON public.bookings(service_id);
