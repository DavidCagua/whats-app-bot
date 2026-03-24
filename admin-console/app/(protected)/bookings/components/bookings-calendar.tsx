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
      <p className="font-semibold truncate leading-tight">{booking.service_name || "Booking"}</p>
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
  const events = useMemo(() => bookings.map(bookingToEvent), [bookings])
  const shouldRenderCalendar = !canFilterByBusiness || Boolean(businessFilter)
  const [currentView, setCurrentView] = useState<View>(Views.WEEK)

  const hasUnassignedBooking = useMemo(
    () => bookings.some((b) => !b.staff_member_id),
    [bookings]
  )

  // Resource mode: show staff columns when multiple staff exist
  const useResources = staffMembers.length > 1
  const resources = useResources
    ? [
        { resourceId: "unassigned", resourceTitle: "Unassigned" },
        ...staffMembers.map((s) => ({ resourceId: s.id, resourceTitle: s.name })),
      ]
    : undefined

  // Week label
  const weekEnd = new Date(weekStart)
  weekEnd.setUTCDate(weekStart.getUTCDate() + 6)
  const fmtUTC = (d: Date, opts: Intl.DateTimeFormatOptions) =>
    d.toLocaleDateString("en-US", { ...opts, timeZone: "UTC" })
  const weekLabel = `${fmtUTC(weekStart, { month: "short", day: "numeric" })} – ${fmtUTC(weekEnd, { month: "short", day: "numeric", year: "numeric" })}`
  const monthLabel = weekStart.toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  })
  const dayLabel = weekStart.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  })
  const periodLabel =
    currentView === Views.MONTH
      ? monthLabel
      : currentView === Views.DAY
        ? dayLabel
        : weekLabel

  function goToPrevPeriod() {
    const prev = new Date(weekStart)
    if (currentView === Views.MONTH) {
      prev.setUTCMonth(prev.getUTCMonth() - 1)
    } else if (currentView === Views.DAY) {
      prev.setUTCDate(prev.getUTCDate() - 1)
    } else {
      prev.setUTCDate(prev.getUTCDate() - 7)
    }
    onWeekChange(prev)
  }

  function goToNextPeriod() {
    const next = new Date(weekStart)
    if (currentView === Views.MONTH) {
      next.setUTCMonth(next.getUTCMonth() + 1)
    } else if (currentView === Views.DAY) {
      next.setUTCDate(next.getUTCDate() + 1)
    } else {
      next.setUTCDate(next.getUTCDate() + 7)
    }
    onWeekChange(next)
  }

  function goToToday() {
    const t = new Date()
    if (currentView === Views.WEEK) {
      const start = new Date(t)
      start.setUTCDate(t.getUTCDate() - t.getUTCDay())
      start.setUTCHours(0, 0, 0, 0)
      onWeekChange(start)
      return
    }
    t.setUTCHours(0, 0, 0, 0)
    onWeekChange(t)
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
                resources={resources}
                resourceIdAccessor={(r: object) => (r as { resourceId: string }).resourceId}
                resourceTitleAccessor={(r: object) => (r as { resourceTitle: string }).resourceTitle}
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
            {(staffMembers.length > 0 || hasUnassignedBooking) && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Staff</span>
                {staffMembers.map((s) => (
                  <div key={s.id} className="flex items-center gap-1.5">
                    <span
                      className="inline-block h-3 w-3 shrink-0 rounded-sm border border-black/10"
                      style={{ backgroundColor: getStaffHex(s.id) }}
                    />
                    <span>{s.name}</span>
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
