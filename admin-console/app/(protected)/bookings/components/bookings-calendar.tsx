"use client"

import { useState } from "react"
import { ChevronLeft, ChevronRight, CalendarDays } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Booking } from "@/lib/bookings-queries"

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
const DAY_NAMES_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
const HOUR_START = 7
const HOUR_END = 21 // exclusive — slots 7:00..20:00

const STATUS_STYLES: Record<string, string> = {
  confirmed: "bg-green-100 border-green-400 text-green-800",
  pending: "bg-yellow-100 border-yellow-400 text-yellow-800",
  cancelled: "bg-gray-100 border-gray-300 text-gray-400 line-through",
  no_show: "bg-red-100 border-red-400 text-red-800",
  completed: "bg-blue-100 border-blue-400 text-blue-800",
}

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "confirmed", label: "Confirmed" },
  { value: "pending", label: "Pending" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "no_show", label: "No Show" },
]

interface BookingsCalendarProps {
  bookings: Booking[]
  weekStart: Date
  businesses: Array<{ id: string; name: string }>
  canFilterByBusiness: boolean
  businessFilter: string
  statusFilter: string
  onWeekChange: (newStart: Date) => void
  onFilterChange: (business: string, status: string) => void
  onCellClick: (date: Date) => void
  onBookingClick: (booking: Booking) => void
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
}

function formatHour(h: number): string {
  if (h === 0) return "12 AM"
  if (h < 12) return `${h} AM`
  if (h === 12) return "12 PM"
  return `${h - 12} PM`
}

export function BookingsCalendar({
  bookings,
  weekStart,
  businesses,
  canFilterByBusiness,
  businessFilter,
  statusFilter,
  onWeekChange,
  onFilterChange,
  onCellClick,
  onBookingClick,
}: BookingsCalendarProps) {
  const [localBusiness, setLocalBusiness] = useState(businessFilter)
  const [localStatus, setLocalStatus] = useState(statusFilter)

  // Build week days
  const days: Date[] = []
  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStart)
    d.setDate(weekStart.getDate() + i)
    days.push(d)
  }

  const today = new Date()

  function goToPrevWeek() {
    const prev = new Date(weekStart)
    prev.setDate(weekStart.getDate() - 7)
    onWeekChange(prev)
  }

  function goToNextWeek() {
    const next = new Date(weekStart)
    next.setDate(weekStart.getDate() + 7)
    onWeekChange(next)
  }

  function goToToday() {
    const t = new Date()
    const day = t.getDay()
    const start = new Date(t)
    start.setDate(t.getDate() - day)
    start.setHours(0, 0, 0, 0)
    onWeekChange(start)
  }

  function handleBusinessChange(val: string) {
    const v = val === "__all__" ? "" : val
    setLocalBusiness(v)
    onFilterChange(v, localStatus)
  }

  function handleStatusChange(val: string) {
    const v = val === "__all__" ? "" : val
    setLocalStatus(v)
    onFilterChange(localBusiness, v)
  }

  // Map bookings to days/hours
  const bookingsByDayHour: Map<string, Booking[]> = new Map()
  for (const booking of bookings) {
    const start = new Date(booking.start_at)
    const dayIdx = days.findIndex((d) => isSameDay(d, start))
    if (dayIdx < 0) continue
    const hour = start.getHours()
    if (hour < HOUR_START || hour >= HOUR_END) continue
    const key = `${dayIdx}-${hour}`
    const existing = bookingsByDayHour.get(key) || []
    existing.push(booking)
    bookingsByDayHour.set(key, existing)
  }

  const hours = Array.from({ length: HOUR_END - HOUR_START }, (_, i) => HOUR_START + i)

  // Format week range label
  const weekEnd = days[6]
  const weekLabel = `${weekStart.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${weekEnd.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`

  return (
    <div className="space-y-4">
      {/* Controls bar */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Navigation */}
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon" onClick={goToPrevWeek}>
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="sm" onClick={goToToday}>
            Today
          </Button>
          <Button variant="outline" size="icon" onClick={goToNextWeek}>
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">{weekLabel}</span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {canFilterByBusiness && businesses.length > 0 && (
            <Select
              value={localBusiness || "__all__"}
              onValueChange={handleBusinessChange}
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

          <Select
            value={localStatus || "__all__"}
            onValueChange={handleStatusChange}
          >
            <SelectTrigger className="w-36 h-8 text-xs">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((opt) => (
                <SelectItem key={opt.value || "__all__"} value={opt.value || "__all__"}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Calendar grid */}
      <div className="rounded-lg border overflow-auto">
        <div className="min-w-[700px]">
          {/* Header row: days */}
          <div className="grid grid-cols-[56px_repeat(7,1fr)] border-b bg-muted/50">
            <div className="p-2 text-xs text-muted-foreground" />
            {days.map((day, i) => {
              const isToday = isSameDay(day, today)
              return (
                <div
                  key={i}
                  className={`p-2 text-center border-l ${isToday ? "bg-primary/10" : ""}`}
                >
                  <p className="text-xs text-muted-foreground">{DAY_NAMES[day.getDay()]}</p>
                  <p
                    className={`text-sm font-semibold ${
                      isToday
                        ? "bg-primary text-primary-foreground rounded-full w-6 h-6 flex items-center justify-center mx-auto"
                        : ""
                    }`}
                  >
                    {day.getDate()}
                  </p>
                </div>
              )
            })}
          </div>

          {/* Hour rows */}
          {hours.map((hour) => (
            <div
              key={hour}
              className="grid grid-cols-[56px_repeat(7,1fr)] border-b last:border-0"
              style={{ minHeight: "64px" }}
            >
              {/* Time label */}
              <div className="p-1 text-right pr-2 pt-1">
                <span className="text-xs text-muted-foreground">{formatHour(hour)}</span>
              </div>

              {/* Day cells */}
              {days.map((day, dayIdx) => {
                const isToday = isSameDay(day, today)
                const cellBookings = bookingsByDayHour.get(`${dayIdx}-${hour}`) || []

                const handleCellClick = () => {
                  const d = new Date(day)
                  d.setHours(hour, 0, 0, 0)
                  onCellClick(d)
                }

                return (
                  <div
                    key={dayIdx}
                    className={`border-l p-1 cursor-pointer hover:bg-muted/30 transition-colors ${
                      isToday ? "bg-primary/5" : ""
                    }`}
                    onClick={handleCellClick}
                  >
                    {cellBookings.map((booking) => (
                      <div
                        key={booking.id}
                        className={`rounded border text-xs p-1 mb-1 cursor-pointer hover:opacity-80 transition-opacity ${
                          STATUS_STYLES[booking.status] || "bg-gray-100 border-gray-300"
                        }`}
                        onClick={(e) => {
                          e.stopPropagation()
                          onBookingClick(booking)
                        }}
                      >
                        <div className="font-medium truncate">
                          {booking.service_name || "Booking"}
                        </div>
                        <div className="text-[10px] opacity-75 truncate">
                          {formatTime(new Date(booking.start_at))} –{" "}
                          {formatTime(new Date(booking.end_at))}
                        </div>
                        {booking.customer && (
                          <div className="text-[10px] opacity-75 truncate">
                            {booking.customer.name}
                          </div>
                        )}
                        <Badge
                          variant="outline"
                          className="text-[9px] px-1 py-0 h-4 mt-0.5"
                        >
                          {booking.status}
                        </Badge>
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {Object.entries(STATUS_STYLES).map(([status, cls]) => (
          <div key={status} className="flex items-center gap-1">
            <div className={`w-3 h-3 rounded border ${cls}`} />
            <span className="capitalize">{status.replace("_", " ")}</span>
          </div>
        ))}
        <span className="ml-2 italic">Click empty cell to create • Click booking to edit</span>
      </div>
    </div>
  )
}
