"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export type OrderStatus = "pending" | "completed" | "cancelled"

function ordersPath(businessId: string) {
  return `/businesses/${businessId}/orders`
}

export async function updateOrderStatus(orderId: string, status: OrderStatus) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.orders.findUnique({ where: { id: orderId } })
  if (!existing) return { success: false as const, error: "Order not found" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const order = await prisma.orders.update({
      where: { id: orderId },
      data: { status, updated_at: new Date() },
    })
    revalidatePath(ordersPath(existing.business_id))
    return { success: true as const, order }
  } catch (err) {
    console.error("updateOrderStatus error:", err)
    return { success: false as const, error: "Failed to update order status" }
  }
}
