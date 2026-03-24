import { Session } from "next-auth"
import { prisma } from "./prisma"
import { isSuperAdmin } from "./permissions"

export type Booking = {
  id: string
  business_id: string
  customer_id: number | null
  service_name: string | null
  start_at: Date
  end_at: Date
  status: string
  notes: string | null
  created_via: string | null
  created_at: Date | null
  staff_member_id: string | null
  staff_member: { id: string; name: string; role: string } | null
  customer: { name: string; whatsapp_id: string } | null
  business: { name: string }
}

export type BookingsAccess = {
  businessIds: string[] | "all"
  businesses: Array<{ id: string; name: string }>
  canFilterByBusiness: boolean
  canManageAvailability: boolean
}

export type AvailabilityRule = {
  id: string
  business_id: string
  day_of_week: number
  open_time: string
  close_time: string
  slot_duration_minutes: number
  is_active: boolean | null
  created_at: Date | null
  updated_at: Date | null
}

/**
 * Get bookings access permissions for the current user
 */
export async function getBookingsAccess(
  session: Session | null
): Promise<BookingsAccess> {
  if (!session?.user) {
    return {
      businessIds: [],
      businesses: [],
      canFilterByBusiness: false,
      canManageAvailability: false,
    }
  }

  // Super admins see everything
  if (isSuperAdmin(session)) {
    const businesses = await prisma.businesses.findMany({
      where: { is_active: true },
      select: { id: true, name: true },
      orderBy: { name: "asc" },
    })

    return {
      businessIds: "all",
      businesses,
      canFilterByBusiness: true,
      canManageAvailability: true,
    }
  }

  // Get user's business associations
  const userBusinesses = session.user.businesses || []
  const businessIds = userBusinesses.map((b) => b.businessId)

  if (businessIds.length === 0) {
    return {
      businessIds: [],
      businesses: [],
      canFilterByBusiness: false,
      canManageAvailability: false,
    }
  }

  const businesses = await prisma.businesses.findMany({
    where: {
      id: { in: businessIds },
      is_active: true,
    },
    select: { id: true, name: true },
    orderBy: { name: "asc" },
  })

  const isAdmin = userBusinesses.some((b) => b.role === "admin")

  return {
    businessIds,
    businesses,
    canFilterByBusiness: businessIds.length > 1,
    canManageAvailability: isAdmin,
  }
}

/**
 * Get bookings with optional filters
 */
export async function getBookings({
  businessIds,
  businessFilter,
  dateFrom,
  dateTo,
  status,
  limit = 200,
}: {
  businessIds: string[] | "all"
  businessFilter?: string
  dateFrom?: Date
  dateTo?: Date
  status?: string
  limit?: number
}): Promise<Booking[]> {
  const whereClause: {
    business_id?: string | { in: string[] }
    start_at?: { gte?: Date; lte?: Date }
    status?: string
  } = {}

  if (businessIds !== "all") {
    whereClause.business_id = { in: businessIds }
  }

  if (businessFilter) {
    whereClause.business_id = businessFilter
  }

  if (dateFrom || dateTo) {
    whereClause.start_at = {}
    if (dateFrom) whereClause.start_at.gte = dateFrom
    if (dateTo) whereClause.start_at.lte = dateTo
  }

  if (status) {
    whereClause.status = status
  }

  const bookings = await prisma.bookings.findMany({
    where: whereClause,
    include: {
      customers: {
        select: { name: true, whatsapp_id: true },
      },
      businesses: {
        select: { name: true },
      },
      staff_members: {
        select: { id: true, name: true, role: true },
      },
    },
    orderBy: { start_at: "asc" },
    take: limit,
  })

  return bookings.map((b) => ({
    id: b.id,
    business_id: b.business_id,
    customer_id: b.customer_id,
    service_name: b.service_name,
    start_at: b.start_at,
    end_at: b.end_at,
    status: b.status,
    notes: b.notes,
    created_via: b.created_via,
    created_at: b.created_at,
    staff_member_id: b.staff_member_id,
    staff_member: b.staff_members
      ? { id: b.staff_members.id, name: b.staff_members.name, role: b.staff_members.role }
      : null,
    customer: b.customers
      ? { name: b.customers.name, whatsapp_id: b.customers.whatsapp_id }
      : null,
    business: { name: b.businesses.name },
  }))
}

/**
 * Get availability rules for a business
 */
export async function getAvailabilityRules(
  businessId: string
): Promise<AvailabilityRule[]> {
  const rules = await prisma.$queryRaw<AvailabilityRule[]>`
    SELECT id::text, business_id::text, day_of_week,
           to_char(open_time, 'HH24:MI') AS open_time,
           to_char(close_time, 'HH24:MI') AS close_time,
           slot_duration_minutes, is_active, created_at, updated_at
    FROM business_availability
    WHERE business_id = ${businessId}::uuid
    ORDER BY day_of_week ASC
  `
  return rules
}
