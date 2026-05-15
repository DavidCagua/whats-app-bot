"use server";

import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { getBookingsAccess } from "@/lib/bookings-queries";
import { revalidatePath } from "next/cache";
import type { Booking } from "@/lib/bookings-queries";

function mapBooking(b: {
  id: string;
  business_id: string;
  customer_id: number | null;
  service_id: string | null;
  start_at: Date;
  end_at: Date;
  status: string;
  notes: string | null;
  created_via: string | null;
  created_at: Date | null;
  staff_member_id: string | null;
  customers: { name: string; whatsapp_id: string } | null;
  businesses: { name: string };
  staff_members: { id: string; name: string; role: string } | null;
  services: {
    id: string;
    name: string;
    price: { toString: () => string };
    duration_minutes: number;
  } | null;
}): Booking {
  return {
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
  };
}

const BOOKING_INCLUDE = {
  customers: { select: { name: true, whatsapp_id: true } },
  businesses: { select: { name: true } },
  staff_members: { select: { id: true, name: true, role: true } },
  services: {
    select: { id: true, name: true, price: true, duration_minutes: true },
  },
} as const;

function revalidateBusinessBookingsPath(businessId: string) {
  revalidatePath(`/businesses/${businessId}/bookings`);
}

async function resolveCustomerId(
  whatsappId: string,
  name?: string,
): Promise<number> {
  let customer = await prisma.customers.findUnique({
    where: { whatsapp_id: whatsappId },
  });
  if (!customer) {
    customer = await prisma.customers.create({
      data: {
        whatsapp_id: whatsappId,
        name: name || whatsappId,
        created_at: new Date(),
        updated_at: new Date(),
      },
    });
  } else if (name && customer.name !== name) {
    customer = await prisma.customers.update({
      where: { whatsapp_id: whatsappId },
      data: { name, updated_at: new Date() },
    });
  }
  return customer.id;
}

/**
 * Accepts either a "HH:MM" / "HH:MM:SS" string or a `Date` (what Prisma
 * returns for a Postgres `TIME` column — epoch date with the clock time
 * in UTC). Returns minutes-from-midnight, or null if the value can't
 * be parsed.
 */
function parseTimeToMinutes(value: string | Date): number | null {
  if (value instanceof Date) {
    return value.getUTCHours() * 60 + value.getUTCMinutes();
  }
  const [h, m] = value.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h * 60 + m;
}

async function isWithinBusinessAvailability(
  businessId: string,
  startAt: Date,
  endAt: Date,
): Promise<boolean> {
  if (!(endAt > startAt)) return false;
  if (startAt.toISOString().slice(0, 10) !== endAt.toISOString().slice(0, 10))
    return false;

  const dayOfWeek = startAt.getUTCDay(); // Sunday=0 ... Saturday=6
  const rule = await prisma.business_availability.findFirst({
    where: {
      business_id: businessId,
      day_of_week: dayOfWeek,
      is_active: true,
    },
  });
  if (!rule) return false;

  const startMins = startAt.getUTCHours() * 60 + startAt.getUTCMinutes();
  const endMins = endAt.getUTCHours() * 60 + endAt.getUTCMinutes();
  const openMins = parseTimeToMinutes(rule.open_time);
  const closeMins = parseTimeToMinutes(rule.close_time);
  if (openMins === null || closeMins === null) return false;

  return startMins >= openMins && endMins <= closeMins;
}

export async function createBooking(data: {
  business_id: string;
  service_id?: string | null;
  start_at: string;
  end_at: string;
  status?: string;
  notes?: string | null;
  staff_member_id?: string | null;
  customer_whatsapp_id?: string;
  customer_name?: string;
}): Promise<
  { success: true; booking: Booking } | { success: false; error: string }
> {
  try {
    const session = await auth();
    if (!session?.user) return { success: false, error: "Unauthorized" };

    const access = await getBookingsAccess(session);
    if (
      access.businessIds !== "all" &&
      !access.businessIds.includes(data.business_id)
    ) {
      return { success: false, error: "Forbidden" };
    }

    let customer_id: number | null = null;
    if (data.customer_whatsapp_id) {
      customer_id = await resolveCustomerId(
        data.customer_whatsapp_id,
        data.customer_name,
      );
    }

    const startAt = new Date(data.start_at);
    const endAt = new Date(data.end_at);
    const withinHours = await isWithinBusinessAvailability(
      data.business_id,
      startAt,
      endAt,
    );
    if (!withinHours) {
      return { success: false, error: "Outside business availability hours" };
    }

    const booking = await prisma.bookings.create({
      data: {
        business_id: data.business_id,
        customer_id,
        service_id: data.service_id || null,
        start_at: startAt,
        end_at: endAt,
        status: data.status || "confirmed",
        notes: data.notes || null,
        staff_member_id: data.staff_member_id || null,
        created_via: "admin",
      },
      include: BOOKING_INCLUDE,
    });

    revalidateBusinessBookingsPath(data.business_id);
    return { success: true, booking: mapBooking(booking) };
  } catch (err) {
    console.error("Error creating booking:", err);
    return { success: false, error: "Failed to create booking" };
  }
}

export async function updateBooking(
  id: string,
  data: {
    service_id?: string | null;
    start_at?: string;
    end_at?: string;
    status?: string;
    notes?: string | null;
    staff_member_id?: string | null;
    customer_whatsapp_id?: string | null;
    customer_name?: string;
  },
): Promise<
  { success: true; booking: Booking } | { success: false; error: string }
> {
  try {
    const session = await auth();
    if (!session?.user) return { success: false, error: "Unauthorized" };

    const existing = await prisma.bookings.findUnique({ where: { id } });
    if (!existing) return { success: false, error: "Not found" };

    const access = await getBookingsAccess(session);
    if (
      access.businessIds !== "all" &&
      !access.businessIds.includes(existing.business_id)
    ) {
      return { success: false, error: "Forbidden" };
    }

    let customer_id: number | null | undefined = undefined;
    if (data.customer_whatsapp_id !== undefined) {
      if (!data.customer_whatsapp_id) {
        customer_id = null;
      } else {
        customer_id = await resolveCustomerId(
          data.customer_whatsapp_id,
          data.customer_name,
        );
      }
    }

    const updateData: Record<string, unknown> = { updated_at: new Date() };
    if (data.service_id !== undefined) updateData.service_id = data.service_id;
    if (data.start_at !== undefined)
      updateData.start_at = new Date(data.start_at);
    if (data.end_at !== undefined) updateData.end_at = new Date(data.end_at);
    if (data.status !== undefined) updateData.status = data.status;
    if (data.notes !== undefined) updateData.notes = data.notes;
    if (data.staff_member_id !== undefined)
      updateData.staff_member_id = data.staff_member_id;
    if (customer_id !== undefined) updateData.customer_id = customer_id;

    const nextStart =
      data.start_at !== undefined ? new Date(data.start_at) : existing.start_at;
    const nextEnd =
      data.end_at !== undefined ? new Date(data.end_at) : existing.end_at;
    const withinHours = await isWithinBusinessAvailability(
      existing.business_id,
      nextStart,
      nextEnd,
    );
    if (!withinHours) {
      return { success: false, error: "Outside business availability hours" };
    }

    const booking = await prisma.bookings.update({
      where: { id },
      data: updateData,
      include: BOOKING_INCLUDE,
    });

    revalidateBusinessBookingsPath(booking.business_id);
    return { success: true, booking: mapBooking(booking) };
  } catch (err) {
    console.error("Error updating booking:", err);
    return { success: false, error: "Failed to update booking" };
  }
}

export async function cancelBooking(
  id: string,
): Promise<{ success: true } | { success: false; error: string }> {
  try {
    const session = await auth();
    if (!session?.user) return { success: false, error: "Unauthorized" };

    const existing = await prisma.bookings.findUnique({ where: { id } });
    if (!existing) return { success: false, error: "Not found" };

    const access = await getBookingsAccess(session);
    if (
      access.businessIds !== "all" &&
      !access.businessIds.includes(existing.business_id)
    ) {
      return { success: false, error: "Forbidden" };
    }

    await prisma.bookings.update({
      where: { id },
      data: { status: "cancelled", updated_at: new Date() },
    });

    revalidateBusinessBookingsPath(existing.business_id);
    return { success: true };
  } catch (err) {
    console.error("Error cancelling booking:", err);
    return { success: false, error: "Failed to cancel booking" };
  }
}

export async function rescheduleBooking(
  id: string,
  newStart: string,
  newEnd: string,
  staffMemberId?: string | null,
): Promise<
  { success: true; booking: Booking } | { success: false; error: string }
> {
  const data: Parameters<typeof updateBooking>[1] = {
    start_at: newStart,
    end_at: newEnd,
  };
  if (staffMemberId !== undefined) data.staff_member_id = staffMemberId;
  return updateBooking(id, data);
}
