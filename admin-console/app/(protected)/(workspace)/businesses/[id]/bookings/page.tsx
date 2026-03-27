import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { canAccessBusiness } from "@/lib/permissions"
import {
  getBookingsAccess,
  getBookings,
  getAvailabilityRules,
} from "@/lib/bookings-queries"
import { prisma } from "@/lib/prisma"
import { BookingsView } from "../_components/bookings/bookings-view"

type SearchParams = {
  dateFrom?: string
  dateTo?: string
}

export default async function BusinessBookingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<SearchParams>
}) {
  const { id: businessId } = await params
  const session = await auth()

  if (!session) redirect("/login")
  if (!canAccessBusiness(session, businessId)) redirect("/businesses")

  const access = await getBookingsAccess(session)
  if (access.businessIds !== "all" && !access.businessIds.includes(businessId)) {
    redirect("/businesses")
  }

  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return (
      <div className="space-y-6">
        <p className="text-center text-muted-foreground py-12">
          No business access configured. Contact your administrator.
        </p>
      </div>
    )
  }

  const paramsQ = await searchParams

  const now = new Date()
  const dayOfWeek = now.getDay()
  const weekStart = new Date(now)
  weekStart.setDate(now.getDate() - dayOfWeek)
  weekStart.setHours(0, 0, 0, 0)
  const weekEnd = new Date(weekStart)
  weekEnd.setDate(weekStart.getDate() + 6)
  weekEnd.setHours(23, 59, 59, 999)

  const dateFrom = paramsQ.dateFrom ? new Date(paramsQ.dateFrom) : weekStart
  const dateTo = paramsQ.dateTo ? new Date(paramsQ.dateTo) : weekEnd

  const [bookings, availabilityRules, initialStaff, businessRow, initialServices] = await Promise.all([
    getBookings({
      businessIds: access.businessIds,
      businessFilter: businessId,
      dateFrom,
      dateTo,
      limit: 500,
    }),
    getAvailabilityRules(businessId),
    prisma.staff_members.findMany({
      where: { business_id: businessId, is_active: true },
      select: { id: true, name: true, role: true },
      orderBy: { name: "asc" },
    }),
    prisma.businesses.findUnique({
      where: { id: businessId },
      select: { id: true, name: true },
    }),
    prisma.services.findMany({
      where: { business_id: businessId, is_active: true },
      select: { id: true, name: true, duration_minutes: true },
      orderBy: { name: "asc" },
    }),
  ])

  const singleBusinessAccess = {
    ...access,
    canFilterByBusiness: false,
    businesses: businessRow
      ? [{ id: businessRow.id, name: businessRow.name }]
      : access.businesses.filter((b) => b.id === businessId),
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Bookings</h1>
        <p className="text-sm text-muted-foreground">
          Calendar and availability for this business
        </p>
      </div>

      <BookingsView
        bookings={bookings}
        access={singleBusinessAccess}
        availabilityRules={availabilityRules}
        fixedBusinessId={businessId}
        initialFilters={{
          business: businessId,
          dateFrom: paramsQ.dateFrom,
          dateTo: paramsQ.dateTo,
        }}
        initialWeekStart={weekStart.toISOString()}
        initialStaff={initialStaff}
        initialServices={initialServices}
      />
    </div>
  )
}
