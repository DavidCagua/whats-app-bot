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
    const staff = await prisma.staff_members.findMany({
      where: { business_id: businessId, is_active: true },
      select: { id: true, name: true, role: true },
      orderBy: { name: "asc" },
    })
    return NextResponse.json(staff)
  } catch (err) {
    console.error("Error fetching staff:", err)
    return NextResponse.json({ error: "Failed to fetch staff" }, { status: 500 })
  }
}
