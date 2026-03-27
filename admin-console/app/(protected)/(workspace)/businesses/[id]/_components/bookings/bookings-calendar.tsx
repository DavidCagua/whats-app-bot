"use client"

import { useMemo, useState } from "react"
import { Calendar, dateFnsLocalizer, Views, type View } from "react-big-calendar"
import withDragAndDrop from "react-big-calendar/lib/addons/dragAndDrop"
import { DndProvider } from "react-dnd"
import { HTML5Backend } from "react-dnd-html5-backend"
import { format, parse, startOfWeek, getDay } from "date-fns"
import { enUS } from "date-fns/locale/en-US"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { Booking } from "@/lib/bookings-queries"
import {
  bookingToEvent,
  fromDisplayDate,
  getStaffHex,
  getStaffEventTextClass,
  type CalendarEvent,
} from "./calendar-utils"

// Localizer — must be at module level
const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek,
  getDay,
  locales: { "en-US": enUS },
})

const DnDCalendar = withDragAndDrop<CalendarEvent>(Calendar)

// Visible hour range
const MIN_TIME = new Date(0, 0, 0, 7, 0)
const MAX_TIME = new Date(0, 0, 0, 21, 0)

export type StaffMember = { id: string; name: string; role: string }

interface BookingsCalendarProps {
  bookings: Booking[]
  weekStart: Date
  businesses: Array<{ id: string; name: string }>
  staffMembers: StaffMember[]
  canFilterByBusiness: boolean
  businessFilter: string
  staffFilter: string
  onWeekChange: (newStart: Date) => void
  onFilterChange: (business: string, staff: string) => void
  onCellClick: (date: Date) => void
  onBookingClick: (booking: Booking) => void
  onBookingReschedule: (id: string, newStart: string, newEnd: string, staffMemberId?: string | null) => Promise<void>
}

// Custom event block rendered inside each calendar slot
function BookingEventBlock({ event }: { event: CalendarEvent }) {
  const booking = event.booking
  const textClass = getStaffEventTextClass(booking.staff_member_id)
  return (
    <div className={`h-full px-1 py-0.5 rounded text-xs overflow-hidden ${textClass}`}>
      <p className="font-semibold truncate leading-tight">{booking.service?.name || "Booking"}</p>
      {booking.customer?.name && (
        <p className="truncate opacity-80">{booking.customer.name}</p>
      )}
      {booking.staff_member?.name && (
        <p className="truncate opacity-60">{booking.staff_member.name}</p>
      )}
    </div>
  )
}

export function BookingsCalendar({
  bookings,
  weekStart,
  businesses,
  staffMembers,
  canFilterByBusiness,
  businessFilter,
  staffFilter,
  onWeekChange,
  onFilterChange,
  onCellClick,
  onBookingClick,
  onBookingReschedule,
}: BookingsCalendarProps) {
  /** Hide cancelled on the grid so stale rows do not mask active bookings. */
  const calendarBookings = useMemo(
    () => bookings.filter((b) => b.status !== "cancelled"),
    [bookings]
  )

  const events = useMemo(
    () => calendarBookings.map(bookingToEvent),
    [calendarBookings]
  )
  const shouldRenderCalendar = !canFilterByBusiness || Boolean(businessFilter)
  const [currentView, setCurrentView] = useState<View>(Views.WEEK)

  const hasUnassignedBooking = useMemo(
    () => calendarBookings.some((b) => !b.staff_member_id),
    [calendarBookings]
  )

  /** Single grid: all bookings in shared day columns (no per-staff resource columns). */
  const legendStaffEntries = useMemo(() => {
    const byId = new Map<string, string>()
    for (const s of staffMembers) {
      byId.set(s.id, s.name)
    }
    for (const b of calendarBookings) {
      if (b.staff_member_id && !byId.has(b.staff_member_id)) {
        byId.set(b.staff_member_id, b.staff_member?.name || "Staff")
      }
    }
    return Array.from(byId.entries())
  }, [staffMembers, calendarBookings])

  // Week label — use local timezone to match react-big-calendar's date interpretation
  const weekEnd = new Date(weekStart)
  weekEnd.setDate(weekStart.getDate() + 6)
  const weekLabel = `${weekStart.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${weekEnd.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`
  const monthLabel = weekStart.toLocaleDateString("en-US", { month: "long", year: "numeric" })
  const dayLabel = weekStart.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" })
  const periodLabel =
    currentView === Views.MONTH
      ? monthLabel
      : currentView === Views.DAY
        ? dayLabel
        : weekLabel

  function goToPrevPeriod() {
    const prev = new Date(weekStart)
    if (currentView === Views.MONTH) {
      prev.setMonth(prev.getMonth() - 1)
    } else if (currentView === Views.DAY) {
      prev.setDate(prev.getDate() - 1)
    } else {
      prev.setDate(prev.getDate() - 7)
    }
    onWeekChange(prev)
  }

  function goToNextPeriod() {
    const next = new Date(weekStart)
    if (currentView === Views.MONTH) {
      next.setMonth(next.getMonth() + 1)
    } else if (currentView === Views.DAY) {
      next.setDate(next.getDate() + 1)
    } else {
      next.setDate(next.getDate() + 7)
    }
    onWeekChange(next)
  }

  function goToToday() {
    const t = new Date()
    if (currentView === Views.WEEK) {
      const start = new Date(t)
      start.setDate(t.getDate() - t.getDay())
      start.setHours(0, 0, 0, 0)
      onWeekChange(start)
      return
    }
    if (currentView === Views.DAY) {
      const d = new Date(t)
      d.setHours(0, 0, 0, 0)
      onWeekChange(d)
      return
    }
    const m = new Date(t.getFullYear(), t.getMonth(), 1)
    m.setHours(0, 0, 0, 0)
    onWeekChange(m)
  }

  return (
    <DndProvider backend={HTML5Backend}>
      <div className="space-y-3">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" onClick={goToPrevPeriod}>
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={goToToday}>
              Today
            </Button>
            <Button variant="outline" size="icon" onClick={goToNextPeriod}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>

          <span className="text-sm font-medium">{periodLabel}</span>

          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant={currentView === Views.MONTH ? "default" : "outline"}
              onClick={() => setCurrentView(Views.MONTH)}
            >
              Month
            </Button>
            <Button
              size="sm"
              variant={currentView === Views.WEEK ? "default" : "outline"}
              onClick={() => setCurrentView(Views.WEEK)}
            >
              Week
            </Button>
            <Button
              size="sm"
              variant={currentView === Views.DAY ? "default" : "outline"}
              onClick={() => setCurrentView(Views.DAY)}
            >
              Day
            </Button>
          </div>

          <div className="ml-auto flex items-center gap-2">
            {canFilterByBusiness && businesses.length > 0 && (
              <Select
                value={businessFilter || "__all__"}
                onValueChange={(v) =>
                  onFilterChange(v === "__all__" ? "" : v, staffFilter)
                }
              >
                <SelectTrigger className="w-44 h-8 text-xs">
                  <SelectValue placeholder="All businesses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All businesses</SelectItem>
                  {businesses.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {staffMembers.length > 0 && (
              <Select
                value={staffFilter || "__all__"}
                onValueChange={(v) =>
                  onFilterChange(businessFilter, v === "__all__" ? "" : v)
                }
              >
                <SelectTrigger className="w-40 h-8 text-xs">
                  <SelectValue placeholder="All staff" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All staff</SelectItem>
                  {staffMembers.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        </div>

        {shouldRenderCalendar ? (
          <>
            {/* Calendar */}
            <div className="rounded-lg border overflow-hidden rbc-wrapper">
              <DnDCalendar
                localizer={localizer}
                events={events}
                defaultView={Views.WEEK}
                views={[Views.MONTH, Views.WEEK, Views.DAY]}
                view={currentView}
                onView={(view) => setCurrentView(view)}
                toolbar={false}
                date={weekStart}
                onNavigate={() => {}}
                min={MIN_TIME}
                max={MAX_TIME}
                step={30}
                timeslots={2}
                style={{ height: 680 }}
                eventPropGetter={(event) => ({
                  style: {
                    backgroundColor: getStaffHex(event.booking.staff_member_id),
                    border: "none",
                    borderRadius: "4px",
                    padding: 0,
                  },
                })}
                components={{
                  event: BookingEventBlock,
                }}
                selectable
                onSelectSlot={({ start }) => {
                  onCellClick(fromDisplayDate(start as Date))
                }}
                onSelectEvent={(event) => {
                  onBookingClick(event.booking)
                }}
                onEventDrop={({ event, start, end }) => {
                  void onBookingReschedule(
                    event.id,
                    fromDisplayDate(start as Date).toISOString(),
                    fromDisplayDate(end as Date).toISOString(),
                    event.booking.staff_member_id
                  )
                }}
                onEventResize={({ event, start, end }) => {
                  void onBookingReschedule(
                    event.id,
                    fromDisplayDate(start as Date).toISOString(),
                    fromDisplayDate(end as Date).toISOString(),
                    event.booking.staff_member_id
                  )
                }}
                resizable
                popup
              />
            </div>

            {/* Staff color legend */}
            {(legendStaffEntries.length > 0 || hasUnassignedBooking) && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Staff</span>
                {legendStaffEntries.map(([id, name]) => (
                  <div key={id} className="flex items-center gap-1.5">
                    <span
                      className="inline-block h-3 w-3 shrink-0 rounded-sm border border-black/10"
                      style={{ backgroundColor: getStaffHex(id) }}
                    />
                    <span>{name}</span>
                  </div>
                ))}
                {hasUnassignedBooking && (
                  <div className="flex items-center gap-1.5">
                    <span
                      className="inline-block h-3 w-3 shrink-0 rounded-sm border border-black/10"
                      style={{ backgroundColor: getStaffHex(null) }}
                    />
                    <span>Unassigned</span>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="rounded-lg border p-8 text-center text-sm text-muted-foreground">
            Select a business to view the calendar.
          </div>
        )}
      </div>
    </DndProvider>
  )
}
