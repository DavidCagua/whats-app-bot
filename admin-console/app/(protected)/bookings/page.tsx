import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { getBookingsAccess, getBookings, getAvailabilityRules } from "@/lib/bookings-queries"
import { BookingsHeader } from "./components/bookings-header"
import { BookingsView } from "./components/bookings-view"

type SearchParams = {
  business?: string
  dateFrom?: string
  dateTo?: string
  status?: string
}

export default async function BookingsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const session = await auth()

  if (!session) {
    redirect("/login")
  }

  const access = await getBookingsAccess(session)

  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return (
      <div className="space-y-6">
        <BookingsHeader bookings={[]} />
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <p className="text-muted-foreground">
            No business access configured. Contact your administrator.
          </p>
        </div>
      </div>
    )
  }

  const params = await searchParams
  const businessFilter = params.business
  const statusFilter = params.status

  // Default to current week
  const now = new Date()
  const dayOfWeek = now.getDay()
  const weekStart = new Date(now)
  weekStart.setDate(now.getDate() - dayOfWeek)
  weekStart.setHours(0, 0, 0, 0)
  const weekEnd = new Date(weekStart)
  weekEnd.setDate(weekStart.getDate() + 6)
  weekEnd.setHours(23, 59, 59, 999)

  const dateFrom = params.dateFrom ? new Date(params.dateFrom) : weekStart
  const dateTo = params.dateTo ? new Date(params.dateTo) : weekEnd

  // Fetch availability for the first accessible business (or filtered one)
  const primaryBusinessId =
    businessFilter ||
    (access.businessIds !== "all" ? access.businessIds[0] : access.businesses[0]?.id)

  const [bookings, availabilityRules] = await Promise.all([
    getBookings({
      businessIds: access.businessIds,
      businessFilter,
      dateFrom,
      dateTo,
      status: statusFilter,
      limit: 500,
    }),
    primaryBusinessId ? getAvailabilityRules(primaryBusinessId) : Promise.resolve([]),
  ])

  return (
    <div className="space-y-6">
      <BookingsHeader bookings={bookings} />

      <BookingsView
        bookings={bookings}
        access={access}
        availabilityRules={availabilityRules}
        initialFilters={{
          business: businessFilter,
          dateFrom: params.dateFrom,
          dateTo: params.dateTo,
          status: statusFilter,
        }}
        initialWeekStart={weekStart.toISOString()}
      />
    </div>
  )
}
