-- Add staff_member_id to bookings table
ALTER TABLE bookings
ADD COLUMN staff_member_id UUID REFERENCES staff_members(id) ON DELETE SET NULL;

-- Add staff_member_id to business_availability table
ALTER TABLE business_availability
ADD COLUMN staff_member_id UUID REFERENCES staff_members(id) ON DELETE CASCADE;

-- Create indexes for efficient queries
CREATE INDEX idx_bookings_staff_member_id ON bookings(staff_member_id);
CREATE INDEX idx_business_availability_staff_member_id ON business_availability(staff_member_id);
CREATE INDEX idx_bookings_staff_business ON bookings(staff_member_id, business_id);
