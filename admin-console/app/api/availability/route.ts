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
    const rules = await prisma.$queryRaw`
      SELECT id::text, business_id::text, day_of_week,
             to_char(open_time, 'HH24:MI') AS open_time,
             to_char(close_time, 'HH24:MI') AS close_time,
             slot_duration_minutes, is_active, created_at, updated_at
      FROM business_availability
      WHERE business_id = ${businessId}::uuid
      ORDER BY day_of_week ASC
    `
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

    await prisma.$executeRaw`
      DELETE FROM business_availability WHERE business_id = ${businessId}::uuid
    `

    for (const rule of body as Array<{
      day_of_week: number
      open_time: string
      close_time: string
      slot_duration_minutes?: number
      is_active?: boolean
    }>) {
      const slotDuration = rule.slot_duration_minutes ?? 60
      const isActive = rule.is_active ?? true
      await prisma.$executeRaw`
        INSERT INTO business_availability
          (id, business_id, day_of_week, open_time, close_time, slot_duration_minutes, is_active, created_at, updated_at)
        VALUES
          (gen_random_uuid(), ${businessId}::uuid, ${rule.day_of_week}::smallint,
           ${rule.open_time}::time, ${rule.close_time}::time, ${slotDuration}::integer, ${isActive}, now(), now())
      `
    }

    const results = await prisma.$queryRaw`
      SELECT id::text, business_id::text, day_of_week,
             to_char(open_time, 'HH24:MI') AS open_time,
             to_char(close_time, 'HH24:MI') AS close_time,
             slot_duration_minutes, is_active, created_at, updated_at
      FROM business_availability
      WHERE business_id = ${businessId}::uuid
      ORDER BY day_of_week ASC
    `

    return NextResponse.json(results)
  } catch (err) {
    console.error("Error updating availability:", err)
    return NextResponse.json({ error: "Failed to update availability" }, { status: 500 })
  }
}
