import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getBookingsAccess } from "@/lib/bookings-queries"
import { prisma } from "@/lib/prisma"

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const access = await getBookingsAccess(session)
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return NextResponse.json({ error: "No business access" }, { status: 403 })
  }

  const searchParams = request.nextUrl.searchParams
  const businessFilter = searchParams.get("business") || undefined
  const dateFromParam = searchParams.get("dateFrom")
  const dateToParam = searchParams.get("dateTo")
  const statusFilter = searchParams.get("status") || undefined
  const limit = Math.min(parseInt(searchParams.get("limit") || "200", 10), 500)

  const dateFrom = dateFromParam ? new Date(dateFromParam) : undefined
  const dateTo = dateToParam ? new Date(dateToParam) : undefined

  // Build where clause
  const whereClause: Record<string, unknown> = {}

  if (access.businessIds !== "all") {
    whereClause.business_id = { in: access.businessIds }
  }
  if (businessFilter) {
    whereClause.business_id = businessFilter
  }
  if (dateFrom || dateTo) {
    whereClause.start_at = {}
    const startAt = whereClause.start_at as Record<string, Date>
    if (dateFrom) startAt.gte = dateFrom
    if (dateTo) startAt.lte = dateTo
  }
  if (statusFilter) {
    whereClause.status = statusFilter
  }

  try {
    const bookings = await prisma.bookings.findMany({
      where: whereClause,
      include: {
        customers: { select: { name: true, whatsapp_id: true } },
        businesses: { select: { name: true } },
      },
      orderBy: { start_at: "asc" },
      take: limit,
    })
    return NextResponse.json(bookings)
  } catch (err) {
    console.error("Error fetching bookings:", err)
    return NextResponse.json({ error: "Failed to fetch bookings" }, { status: 500 })
  }
}

export async function POST(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const access = await getBookingsAccess(session)
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return NextResponse.json({ error: "No business access" }, { status: 403 })
  }

  try {
    const body = await request.json()
    const {
      business_id,
      service_name,
      start_at,
      end_at,
      status = "confirmed",
      notes,
      customer_whatsapp_id,
      customer_name,
    } = body

    if (!business_id || !start_at || !end_at) {
      return NextResponse.json(
        { error: "business_id, start_at, and end_at are required" },
        { status: 400 }
      )
    }

    // Check access to this business
    if (
      access.businessIds !== "all" &&
      !access.businessIds.includes(business_id)
    ) {
      return NextResponse.json({ error: "Forbidden" }, { status: 403 })
    }

    // Resolve or create customer if whatsapp_id provided
    let customer_id: number | null = null
    if (customer_whatsapp_id) {
      let customer = await prisma.customers.findUnique({
        where: { whatsapp_id: customer_whatsapp_id },
      })
      if (!customer) {
        customer = await prisma.customers.create({
          data: {
            whatsapp_id: customer_whatsapp_id,
            name: customer_name || customer_whatsapp_id,
            created_at: new Date(),
            updated_at: new Date(),
          },
        })
      } else if (customer_name && customer.name !== customer_name) {
        customer = await prisma.customers.update({
          where: { whatsapp_id: customer_whatsapp_id },
          data: { name: customer_name, updated_at: new Date() },
        })
      }
      customer_id = customer.id
    }

    const booking = await prisma.bookings.create({
      data: {
        business_id,
        customer_id,
        service_name: service_name || null,
        start_at: new Date(start_at),
        end_at: new Date(end_at),
        status,
        notes: notes || null,
        created_via: "admin",
      },
      include: {
        customers: { select: { name: true, whatsapp_id: true } },
        businesses: { select: { name: true } },
      },
    })

    return NextResponse.json(booking, { status: 201 })
  } catch (err) {
    console.error("Error creating booking:", err)
    return NextResponse.json({ error: "Failed to create booking" }, { status: 500 })
  }
}
