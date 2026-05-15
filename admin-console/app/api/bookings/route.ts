import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getBookingsAccess } from "@/lib/bookings-queries";
import { prisma } from "@/lib/prisma";

export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const access = await getBookingsAccess(session);
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return NextResponse.json({ error: "No business access" }, { status: 403 });
  }

  const searchParams = request.nextUrl.searchParams;
  const businessFilter = searchParams.get("business") || undefined;
  const dateFromParam = searchParams.get("dateFrom");
  const dateToParam = searchParams.get("dateTo");
  const statusFilter = searchParams.get("status") || undefined;
  const staffFilter = searchParams.get("staff") || undefined;
  const limit = Math.min(parseInt(searchParams.get("limit") || "200", 10), 500);

  const dateFrom = dateFromParam ? new Date(dateFromParam) : undefined;
  const dateTo = dateToParam ? new Date(dateToParam) : undefined;

  const whereClause: Record<string, unknown> = {};

  if (access.businessIds !== "all") {
    whereClause.business_id = { in: access.businessIds };
  }
  if (businessFilter) {
    whereClause.business_id = businessFilter;
  }
  if (dateFrom || dateTo) {
    whereClause.start_at = {};
    const startAt = whereClause.start_at as Record<string, Date>;
    if (dateFrom) startAt.gte = dateFrom;
    if (dateTo) startAt.lte = dateTo;
  }
  if (statusFilter) {
    whereClause.status = statusFilter;
  }
  if (staffFilter) {
    whereClause.staff_member_id = staffFilter;
  }

  try {
    const bookings = await prisma.bookings.findMany({
      where: whereClause,
      include: {
        customers: { select: { name: true, whatsapp_id: true } },
        businesses: { select: { name: true } },
        staff_members: { select: { id: true, name: true, role: true } },
        services: {
          select: { id: true, name: true, price: true, duration_minutes: true },
        },
      },
      orderBy: { start_at: "asc" },
      take: limit,
    });

    return NextResponse.json(
      bookings.map((b) => ({
        id: b.id,
        business_id: b.business_id,
        customer_id: b.customer_id,
        service_id: b.service_id,
        service: b.services
          ? {
              id: b.services.id,
              name: b.services.name,
              price: Number(b.services.price.toString()),
              duration_minutes: b.services.duration_minutes,
            }
          : null,
        start_at: b.start_at,
        end_at: b.end_at,
        status: b.status,
        notes: b.notes,
        created_via: b.created_via,
        created_at: b.created_at,
        staff_member_id: b.staff_member_id,
        staff_member: b.staff_members
          ? {
              id: b.staff_members.id,
              name: b.staff_members.name,
              role: b.staff_members.role,
            }
          : null,
        customer: b.customers
          ? { name: b.customers.name, whatsapp_id: b.customers.whatsapp_id }
          : null,
        business: { name: b.businesses.name },
      })),
    );
  } catch (err) {
    console.error("Error fetching bookings:", err);
    return NextResponse.json(
      { error: "Failed to fetch bookings" },
      { status: 500 },
    );
  }
}
