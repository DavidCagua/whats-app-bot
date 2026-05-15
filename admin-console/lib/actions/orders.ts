"use server";

import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canEditBusiness } from "@/lib/permissions";
import { revalidatePath } from "next/cache";
import {
  type OrderStatus,
  adminCanTransition,
  isValidStatus,
  timestampFieldFor,
} from "@/lib/order-status";

function ordersPath(businessId: string) {
  return `/businesses/${businessId}/orders`;
}

type UpdateOptions = {
  cancellationReason?: string | null;
};

export async function updateOrderStatus(
  orderId: string,
  status: OrderStatus,
  options: UpdateOptions = {},
) {
  const session = await auth();
  if (!session?.user) return { success: false as const, error: "Unauthorized" };

  if (!isValidStatus(status)) {
    return { success: false as const, error: `Invalid status: ${status}` };
  }

  const existing = await prisma.orders.findUnique({ where: { id: orderId } });
  if (!existing) return { success: false as const, error: "Order not found" };
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" };
  }

  if (existing.status === status) {
    return { success: true as const };
  }

  // Admin-only loose machine: any-to-any except no-op (already filtered
  // above). Keeps the bot's strict transitions untouched.
  if (!adminCanTransition(existing.status, status)) {
    return {
      success: false as const,
      error: `Estado inválido: "${status}"`,
    };
  }

  const now = new Date();
  const tsField = timestampFieldFor(status);

  // Build update payload. We assign the lifecycle timestamp the first
  // time we enter that state — never overwrite an existing one.
  const data: Record<string, unknown> = {
    status,
    updated_at: now,
  };
  if (tsField) {
    const current = (existing as Record<string, unknown>)[tsField];
    if (!current) data[tsField] = now;
  }
  if (status === "cancelled") {
    data.cancellation_reason = options.cancellationReason ?? null;
    // Admin-initiated cancel — always 'business'. Customer cancels go
    // through the bot path (order_modification_service.cancel_order)
    // which stamps 'customer' on the same column.
    data.cancelled_by = "business";
  }

  try {
    await prisma.orders.update({
      where: { id: orderId },
      data,
    });
    revalidatePath(ordersPath(existing.business_id));
    return { success: true as const };
  } catch (err) {
    console.error("updateOrderStatus error:", err);
    return { success: false as const, error: "Failed to update order status" };
  }
}
