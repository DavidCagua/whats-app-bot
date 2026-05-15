"use server";

import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { getBookingsAccess } from "@/lib/bookings-queries";
import type { AvailabilityRule } from "@/lib/bookings-queries";

export type AvailabilityRuleInput = {
  day_of_week: number;
  open_time: string;
  close_time: string;
  slot_duration_minutes?: number;
  is_active?: boolean;
};

export async function saveAvailability(
  businessId: string,
  rules: AvailabilityRuleInput[],
): Promise<
  | { success: true; rules: AvailabilityRule[] }
  | { success: false; error: string }
> {
  try {
    const session = await auth();
    if (!session?.user) return { success: false, error: "Unauthorized" };

    const access = await getBookingsAccess(session);
    if (
      access.businessIds !== "all" &&
      !access.businessIds.includes(businessId)
    ) {
      return { success: false, error: "Forbidden" };
    }

    if (!access.canManageAvailability) {
      return { success: false, error: "Insufficient permissions" };
    }

    await prisma.$executeRaw`
      DELETE FROM business_availability WHERE business_id = ${businessId}::uuid
    `;

    for (const rule of rules) {
      const slotDuration = rule.slot_duration_minutes ?? 60;
      const isActive = rule.is_active ?? true;
      await prisma.$executeRaw`
        INSERT INTO business_availability
          (id, business_id, day_of_week, open_time, close_time, slot_duration_minutes, is_active, created_at, updated_at)
        VALUES
          (gen_random_uuid(), ${businessId}::uuid, ${rule.day_of_week}::smallint,
           ${rule.open_time}::time, ${rule.close_time}::time, ${slotDuration}::integer, ${isActive}, now(), now())
      `;
    }

    const updated = await prisma.$queryRaw<AvailabilityRule[]>`
      SELECT id::text, business_id::text, day_of_week,
             to_char(open_time, 'HH24:MI') AS open_time,
             to_char(close_time, 'HH24:MI') AS close_time,
             slot_duration_minutes, is_active, created_at, updated_at
      FROM business_availability
      WHERE business_id = ${businessId}::uuid
      ORDER BY day_of_week ASC
    `;

    return { success: true, rules: updated };
  } catch (err) {
    console.error("Error saving availability:", err);
    return { success: false, error: "Failed to save availability" };
  }
}
