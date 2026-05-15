import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getBookingsAccess } from "@/lib/bookings-queries";
import { prisma } from "@/lib/prisma";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;

  const booking = await prisma.bookings.findUnique({
    where: { id },
    include: {
      customers: { select: { name: true, whatsapp_id: true } },
      businesses: { select: { name: true } },
      staff_members: { select: { id: true, name: true, role: true } },
      services: {
        select: { id: true, name: true, price: true, duration_minutes: true },
      },
    },
  });

  if (!booking) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const access = await getBookingsAccess(session);
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(booking.business_id)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  return NextResponse.json({
    id: booking.id,
    business_id: booking.business_id,
    customer_id: booking.customer_id,
    service_id: booking.service_id,
    start_at: booking.start_at,
    end_at: booking.end_at,
    status: booking.status,
    notes: booking.notes,
    created_via: booking.created_via,
    created_at: booking.created_at,
    staff_member_id: booking.staff_member_id,
    service: booking.services
      ? {
          id: booking.services.id,
          name: booking.services.name,
          price: Number(booking.services.price.toString()),
          duration_minutes: booking.services.duration_minutes,
        }
      : null,
    staff_member: booking.staff_members
      ? {
          id: booking.staff_members.id,
          name: booking.staff_members.name,
          role: booking.staff_members.role,
        }
      : null,
    customer: booking.customers
      ? {
          name: booking.customers.name,
          whatsapp_id: booking.customers.whatsapp_id,
        }
      : null,
    business: { name: booking.businesses.name },
  });
}
