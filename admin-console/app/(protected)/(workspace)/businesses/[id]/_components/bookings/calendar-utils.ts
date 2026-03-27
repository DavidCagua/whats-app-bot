import type { Booking } from "@/lib/bookings-queries"

export type CalendarEvent = {
  id: string
  title: string
  start: Date
  end: Date
  resourceId: string
  booking: Booking
}

/** Pastel backgrounds + dark text for contrast on event cards */
const STAFF_EVENT_PALETTE: { hex: string; text: string }[] = [
  { hex: "#c7d2fe", text: "text-indigo-950" },
  { hex: "#fde68a", text: "text-amber-950" },
  { hex: "#bbf7d0", text: "text-green-950" },
  { hex: "#fbcfe8", text: "text-pink-950" },
  { hex: "#a5f3fc", text: "text-cyan-950" },
  { hex: "#e9d5ff", text: "text-purple-950" },
  { hex: "#fed7aa", text: "text-orange-950" },
  { hex: "#fecdd3", text: "text-rose-950" },
  { hex: "#ddd6fe", text: "text-violet-950" },
  { hex: "#99f6e4", text: "text-teal-950" },
]

const UNASSIGNED_STAFF = { hex: "#e5e7eb", text: "text-gray-800" }

function hashStaffId(id: string): number {
  let h = 0
  for (let i = 0; i < id.length; i++) {
    h = (Math.imul(31, h) + id.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}

export function getStaffColorEntry(
  staffMemberId: string | null | undefined
): { hex: string; text: string } {
  if (!staffMemberId || staffMemberId === "unassigned") {
    return UNASSIGNED_STAFF
  }
  const i = hashStaffId(staffMemberId) % STAFF_EVENT_PALETTE.length
  return STAFF_EVENT_PALETTE[i]
}

export function getStaffHex(staffMemberId: string | null | undefined): string {
  return getStaffColorEntry(staffMemberId).hex
}

export function getStaffEventTextClass(
  staffMemberId: string | null | undefined
): string {
  return getStaffColorEntry(staffMemberId).text
}

/**
 * Shift a UTC date forward by the local UTC offset so that RBC's local-time
 * methods (.getHours(), .getDate()) read the UTC value.
 *
 * Example: UTC 10:00, browser at UTC-5 (offset = -300 min)
 *   toDisplayDate adds 300 min → local time reads as 10:00 ✓
 */
export function toDisplayDate(utcDate: Date): Date {
  const offsetMs = new Date().getTimezoneOffset() * 60 * 1000
  return new Date(utcDate.getTime() + offsetMs)
}

/**
 * Reverse of toDisplayDate — converts a display-shifted date back to real UTC.
 * Use before sending dates to the API.
 */
export function fromDisplayDate(displayDate: Date): Date {
  const offsetMs = new Date().getTimezoneOffset() * 60 * 1000
  return new Date(displayDate.getTime() - offsetMs)
}

export function bookingToEvent(booking: Booking): CalendarEvent {
  const label = [booking.service?.name, booking.customer?.name]
    .filter(Boolean)
    .join(" — ") || "Booking"

  return {
    id: booking.id,
    title: label,
    start: toDisplayDate(new Date(booking.start_at)),
    end: toDisplayDate(new Date(booking.end_at)),
    resourceId: booking.staff_member_id ?? "unassigned",
    booking,
  }
}
