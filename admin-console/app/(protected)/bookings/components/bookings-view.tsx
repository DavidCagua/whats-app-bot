"use client"

import { useState } from "react"
import { Booking, BookingsAccess, AvailabilityRule } from "@/lib/bookings-queries"
import { BookingsCalendar } from "./bookings-calendar"
import { BookingModal } from "./booking-modal"
import { AvailabilitySettings } from "./availability-settings"
import { Button } from "@/components/ui/button"
import { Settings } from "lucide-react"

interface InitialFilters {
  business?: string
  dateFrom?: string
  dateTo?: string
  status?: string
}

interface BookingsViewProps {
  bookings: Booking[]
  access: BookingsAccess
  availabilityRules: AvailabilityRule[]
  initialFilters: InitialFilters
  initialWeekStart: string
}

export type ModalState =
  | { mode: "closed" }
  | { mode: "create"; date: Date }
  | { mode: "edit"; booking: Booking }

export function BookingsView({
  bookings: initialBookings,
  access,
  availabilityRules: initialRules,
  initialFilters,
  initialWeekStart,
}: BookingsViewProps) {
  const [bookings, setBookings] = useState<Booking[]>(initialBookings)
  const [availabilityRules, setAvailabilityRules] = useState<AvailabilityRule[]>(initialRules)
  const [modalState, setModalState] = useState<ModalState>({ mode: "closed" })
  const [showAvailability, setShowAvailability] = useState(false)
  const [businessFilter, setBusinessFilter] = useState(initialFilters.business || "")
  const [statusFilter, setStatusFilter] = useState(initialFilters.status || "")
  const [weekStart, setWeekStart] = useState(() => new Date(initialWeekStart))

  // Reload bookings from API when filters change
  async function loadBookings(params: {
    business?: string
    dateFrom: Date
    dateTo: Date
    status?: string
  }) {
    const url = new URL("/api/bookings", window.location.origin)
    if (params.business) url.searchParams.set("business", params.business)
    if (params.status) url.searchParams.set("status", params.status)
    url.searchParams.set("dateFrom", params.dateFrom.toISOString())
    url.searchParams.set("dateTo", params.dateTo.toISOString())

    const res = await fetch(url.toString())
    if (res.ok) {
      const data = await res.json()
      setBookings(data)
    }
  }

  function getWeekEnd(start: Date): Date {
    const end = new Date(start)
    end.setDate(start.getDate() + 6)
    end.setHours(23, 59, 59, 999)
    return end
  }

  function handleWeekChange(newStart: Date) {
    setWeekStart(newStart)
    loadBookings({
      business: businessFilter || undefined,
      dateFrom: newStart,
      dateTo: getWeekEnd(newStart),
      status: statusFilter || undefined,
    })
  }

  function handleFilterChange(business: string, status: string) {
    setBusinessFilter(business)
    setStatusFilter(status)
    loadBookings({
      business: business || undefined,
      dateFrom: weekStart,
      dateTo: getWeekEnd(weekStart),
      status: status || undefined,
    })
  }

  function handleBookingSaved(savedBooking: Booking) {
    setBookings((prev) => {
      const idx = prev.findIndex((b) => b.id === savedBooking.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = savedBooking
        return next
      }
      return [...prev, savedBooking]
    })
    setModalState({ mode: "closed" })
  }

  function handleBookingDeleted(id: string) {
    setBookings((prev) => prev.filter((b) => b.id !== id))
    setModalState({ mode: "closed" })
  }

  // Pick business for availability settings
  const availabilityBusinessId =
    businessFilter ||
    (access.businessIds !== "all" ? access.businessIds[0] : access.businesses[0]?.id) ||
    ""

  return (
    <div className="space-y-4">
      {/* Availability toggle (admin only) */}
      {access.canManageAvailability && availabilityBusinessId && (
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowAvailability((v) => !v)}
          >
            <Settings className="h-4 w-4 mr-2" />
            {showAvailability ? "Hide" : "Show"} Availability Settings
          </Button>
        </div>
      )}

      {showAvailability && access.canManageAvailability && availabilityBusinessId && (
        <AvailabilitySettings
          businessId={availabilityBusinessId}
          initialRules={availabilityRules}
          onRulesUpdated={setAvailabilityRules}
        />
      )}

      <BookingsCalendar
        bookings={bookings}
        weekStart={weekStart}
        businesses={access.businesses}
        canFilterByBusiness={access.canFilterByBusiness}
        businessFilter={businessFilter}
        statusFilter={statusFilter}
        onWeekChange={handleWeekChange}
        onFilterChange={handleFilterChange}
        onCellClick={(date) => setModalState({ mode: "create", date })}
        onBookingClick={(booking) => setModalState({ mode: "edit", booking })}
      />

      {modalState.mode !== "closed" && (
        <BookingModal
          mode={modalState.mode === "create" ? "create" : "edit"}
          booking={modalState.mode === "edit" ? modalState.booking : undefined}
          initialDate={modalState.mode === "create" ? modalState.date : undefined}
          businesses={access.businesses}
          defaultBusinessId={
            businessFilter ||
            (access.businessIds !== "all" ? access.businessIds[0] : access.businesses[0]?.id) ||
            ""
          }
          onClose={() => setModalState({ mode: "closed" })}
          onSaved={handleBookingSaved}
          onDeleted={handleBookingDeleted}
        />
      )}
    </div>
  )
}
