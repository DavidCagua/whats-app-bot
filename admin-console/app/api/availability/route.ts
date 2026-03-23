import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getBookingsAccess } from "@/lib/bookings-queries"
import { prisma } from "@/lib/prisma"

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const businessId = request.nextUrl.searchParams.get("business_id")
  if (!businessId) {
    return NextResponse.json({ error: "business_id is required" }, { status: 400 })
  }

  const access = await getBookingsAccess(session)
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(businessId)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 })
  }

  try {
    const rules = await prisma.business_availability.findMany({
      where: { business_id: businessId },
      orderBy: { day_of_week: "asc" },
    })
    return NextResponse.json(rules)
  } catch (err) {
    console.error("Error fetching availability:", err)
    return NextResponse.json({ error: "Failed to fetch availability" }, { status: 500 })
  }
}

export async function PUT(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const businessId = request.nextUrl.searchParams.get("business_id")
  if (!businessId) {
    return NextResponse.json({ error: "business_id is required" }, { status: 400 })
  }

  const access = await getBookingsAccess(session)
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(businessId)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 })
  }

  if (!access.canManageAvailability) {
    return NextResponse.json({ error: "Insufficient permissions" }, { status: 403 })
  }

  try {
    const body = await request.json()
    // body: Array<{ day_of_week, open_time, close_time, slot_duration_minutes, is_active }>
    if (!Array.isArray(body)) {
      return NextResponse.json({ error: "Body must be an array" }, { status: 400 })
    }

    const results = await Promise.all(
      body.map((rule: {
        day_of_week: number
        open_time: string
        close_time: string
        slot_duration_minutes?: number
        is_active?: boolean
      }) =>
        prisma.business_availability.upsert({
          where: {
            business_id_day_of_week: {
              business_id: businessId,
              day_of_week: rule.day_of_week,
            },
          },
          update: {
            open_time: rule.open_time,
            close_time: rule.close_time,
            slot_duration_minutes: rule.slot_duration_minutes ?? 60,
            is_active: rule.is_active ?? true,
            updated_at: new Date(),
          },
          create: {
            business_id: businessId,
            day_of_week: rule.day_of_week,
            open_time: rule.open_time,
            close_time: rule.close_time,
            slot_duration_minutes: rule.slot_duration_minutes ?? 60,
            is_active: rule.is_active ?? true,
          },
        })
      )
    )

    return NextResponse.json(results)
  } catch (err) {
    console.error("Error updating availability:", err)
    return NextResponse.json({ error: "Failed to update availability" }, { status: 500 })
  }
}
