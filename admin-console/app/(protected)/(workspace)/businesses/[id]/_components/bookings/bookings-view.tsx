"use client"

import { useState, useRef } from "react"
import dynamic from "next/dynamic"
import { Booking, BookingsAccess, AvailabilityRule } from "@/lib/bookings-queries"
import { rescheduleBooking } from "@/lib/actions/bookings"
import type { StaffMember } from "./bookings-calendar"

const BookingsCalendar = dynamic(
  () => import("./bookings-calendar").then((m) => m.BookingsCalendar),
  { ssr: false }
)
import { BookingModal } from "./booking-modal"
import { AvailabilitySettings } from "./availability-settings"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useIsMobile } from "@/hooks/use-mobile"
import { Settings } from "lucide-react"

interface InitialFilters {
  business?: string
  dateFrom?: string
  dateTo?: string
}

interface BookingsViewProps {
  bookings: Booking[]
  access: BookingsAccess
  availabilityRules: AvailabilityRule[]
  initialFilters: InitialFilters
  initialWeekStart: string
  initialStaff: StaffMember[]
  /** When set, all loads are scoped to this business (workspace route). */
  fixedBusinessId?: string
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
  initialStaff,
  fixedBusinessId,
}: BookingsViewProps) {
  const [bookings, setBookings] = useState<Booking[]>(initialBookings)
  const [availabilityRules, setAvailabilityRules] = useState<AvailabilityRule[]>(initialRules)
  const staffMembers = initialStaff
  const [modalState, setModalState] = useState<ModalState>({ mode: "closed" })
  const [availabilityOpen, setAvailabilityOpen] = useState(false)
  const isMobile = useIsMobile()
  const [businessFilter, setBusinessFilter] = useState(
    fixedBusinessId || initialFilters.business || ""
  )
  const [staffFilter, setStaffFilter] = useState("")
  const [weekStart, setWeekStart] = useState(() => {
    const [y, mo, d] = initialWeekStart.slice(0, 10).split("-").map(Number)
    return new Date(y, mo - 1, d) // local midnight — keeps react-big-calendar and labels in sync
  })

  const effectiveBusinessId =
    fixedBusinessId || businessFilter || undefined

  const loadAbortRef = useRef<AbortController | null>(null)

  // Reload bookings from API when filters/week change
  async function loadBookings(params: {
    business?: string
    dateFrom: Date
    dateTo: Date
    staff?: string
  }) {
    loadAbortRef.current?.abort()
    const controller = new AbortController()
    loadAbortRef.current = controller

    const url = new URL("/api/bookings", window.location.origin)
    const biz = fixedBusinessId || params.business
    if (biz) url.searchParams.set("business", biz)
    if (params.staff) url.searchParams.set("staff", params.staff)
    url.searchParams.set("dateFrom", params.dateFrom.toISOString())
    url.searchParams.set("dateTo", params.dateTo.toISOString())

    try {
      const res = await fetch(url.toString(), { signal: controller.signal })
      if (res.ok) {
        const data = await res.json()
        setBookings(data.map((b: Booking) => ({
          ...b,
          start_at: new Date(b.start_at),
          end_at: new Date(b.end_at),
          created_at: b.created_at ? new Date(b.created_at) : null,
        })))
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return
    }
  }

  const availabilityBusinessId = fixedBusinessId || access.businesses[0]?.id || ""

  /** Match server /bookings page: local calendar week (same as setDate + setHours). */
  function getWeekEnd(start: Date): Date {
    const end = new Date(start)
    end.setDate(start.getDate() + 6)
    end.setHours(23, 59, 59, 999)
    return end
  }

  function handleWeekChange(newStart: Date) {
    setWeekStart(newStart)
    loadBookings({
      business: effectiveBusinessId,
      dateFrom: newStart,
      dateTo: getWeekEnd(newStart),
      staff: staffFilter || undefined,
    })
  }

  function handleFilterChange(business: string, staff: string) {
    if (!fixedBusinessId) setBusinessFilter(business)
    setStaffFilter(staff)
    loadBookings({
      business: (fixedBusinessId || business) || undefined,
      dateFrom: weekStart,
      dateTo: getWeekEnd(weekStart),
      staff: staff || undefined,
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

  async function handleBookingReschedule(
    id: string,
    newStart: string,
    newEnd: string,
    staffMemberId?: string | null
  ) {
    // Optimistic update
    setBookings((prev) =>
      prev.map((b) =>
        b.id === id
          ? { ...b, start_at: new Date(newStart), end_at: new Date(newEnd) }
          : b
      )
    )
    const result = await rescheduleBooking(id, newStart, newEnd, staffMemberId)
    if (result.success) {
      setBookings((prev) =>
        prev.map((b) => (b.id === id ? result.booking : b))
      )
    } else {
      // Revert on failure by reloading
      loadBookings({
        business: effectiveBusinessId,
        dateFrom: weekStart,
        dateTo: getWeekEnd(weekStart),
        staff: staffFilter || undefined,
      })
    }
  }

  return (
    <div className="space-y-4">
      {access.canManageAvailability && availabilityBusinessId && (
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAvailabilityOpen(true)}
          >
            <Settings className="h-4 w-4 mr-2" />
            Availability
          </Button>
        </div>
      )}

      {access.canManageAvailability && (
        <Sheet open={availabilityOpen} onOpenChange={setAvailabilityOpen}>
          <SheetContent
            side={isMobile ? "bottom" : "right"}
            className="flex min-h-0 w-full flex-col gap-0 overflow-hidden p-0 max-sm:max-h-[90dvh] max-sm:rounded-t-xl sm:h-full sm:max-h-[100dvh] sm:max-w-2xl"
          >
            <SheetHeader className="shrink-0 space-y-1 border-b px-4 pb-3 pt-6 text-left pr-12">
              <SheetTitle>Business hours &amp; availability</SheetTitle>
              <SheetDescription>
                Open hours and slot length for this business. The calendar stays visible behind this panel.
              </SheetDescription>
            </SheetHeader>
            {availabilityBusinessId && (
              <AvailabilitySettings
                embedded
                businessId={availabilityBusinessId}
                initialRules={availabilityRules}
                onRulesUpdated={setAvailabilityRules}
              />
            )}
          </SheetContent>
        </Sheet>
      )}

      <BookingsCalendar
        bookings={bookings}
        weekStart={weekStart}
        businesses={access.businesses}
        staffMembers={staffMembers}
        canFilterByBusiness={access.canFilterByBusiness}
        businessFilter={businessFilter}
        staffFilter={staffFilter}
        onWeekChange={handleWeekChange}
        onFilterChange={handleFilterChange}
        onCellClick={(date) => setModalState({ mode: "create", date })}
        onBookingClick={(booking) => setModalState({ mode: "edit", booking })}
        onBookingReschedule={handleBookingReschedule}
      />

      {modalState.mode !== "closed" && (
        <BookingModal
          mode={modalState.mode === "create" ? "create" : "edit"}
          booking={modalState.mode === "edit" ? modalState.booking : undefined}
          initialDate={modalState.mode === "create" ? modalState.date : undefined}
          businesses={access.businesses}
          defaultBusinessId={fixedBusinessId || access.businesses[0]?.id || ""}
          staffMembers={staffMembers}
          onClose={() => setModalState({ mode: "closed" })}
          onSaved={handleBookingSaved}
          onDeleted={handleBookingDeleted}
        />
      )}
    </div>
  )
}
