"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

type Result =
  | { success: true; orderId: string }
  | { success: false; error: string }

/**
 * Mark an order's pending operator edit as reviewed.
 *
 * Stamps ``orders.last_edit_acknowledged_at`` with the current time so
 * the orders table no longer flags the row as "edited — needs review".
 *
 * Global ack (not per-user): the first operator to click clears the
 * warning for everyone. A subsequent edit will bump ``last_edited_at``
 * and re-arm the warning automatically — see the orders-queries
 * ``hasUnackedEdit`` comparison.
 *
 * Permission: same gate as orders-update; the user must be allowed to
 * edit the business that owns the order.
 */
export async function acknowledgeOrderEdit(
  orderId: string,
): Promise<Result> {
  const session = await auth()
  if (!session?.user) return { success: false, error: "Unauthorized" }

  if (!orderId || typeof orderId !== "string") {
    return { success: false, error: "orderId requerido" }
  }

  const existing = await prisma.orders.findUnique({
    where: { id: orderId },
    select: {
      id: true,
      business_id: true,
      status: true,
      last_edited_at: true,
    },
  })
  if (!existing) return { success: false, error: "Pedido no encontrado" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false, error: "Forbidden" }
  }
  if (existing.last_edited_at === null) {
    // No edit to acknowledge. Treat as a no-op success so the UI can
    // optimistically clear without surfacing a confusing error.
    return { success: true, orderId }
  }

  await prisma.orders.update({
    where: { id: orderId },
    data: { last_edit_acknowledged_at: new Date() },
  })

  // Same revalidation surface as updateOrder so the orders table
  // re-fetches with the cleared warning.
  revalidatePath(`/businesses/${existing.business_id}/orders`)

  return { success: true, orderId }
}
