import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getBookingsAccess } from "@/lib/bookings-queries";
import { prisma } from "@/lib/prisma";

export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const businessId = request.nextUrl.searchParams.get("business_id");
  if (!businessId) {
    return NextResponse.json(
      { error: "business_id is required" },
      { status: 400 },
    );
  }

  const access = await getBookingsAccess(session);
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(businessId)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
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
    `;
    return NextResponse.json(rules);
  } catch (err) {
    console.error("Error fetching availability:", err);
    return NextResponse.json(
      { error: "Failed to fetch availability" },
      { status: 500 },
    );
  }
}
