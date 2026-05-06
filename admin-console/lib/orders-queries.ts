import { prisma } from "./prisma"
import type { Prisma } from "@prisma/client"

export type OrderRow = {
  id: string
  created_at: string | null
  whatsapp_id: string | null
  customer_id: number | null
  customer_name: string | null
  delivery_address: string | null
  payment_method: string | null
  total_amount: number
  subtotal: number
  delivery_fee: number
  status: string
  items: {
    id: string
    quantity: number
    productName: string
    notes: string | null
    unitPrice: number
    lineTotal: number
  }[]
}

/**
 * List orders for a business in newest-first order with their items.
 * Shared by the orders RSC and the SSE snapshot so both produce
 * identical row shapes.
 *
 * When `range` is supplied, only orders with `created_at` inside the
 * (UTC) interval are returned. Both ends are inclusive.
 */
export async function getOrdersForBusiness(
  businessId: string,
  range?: { fromUtc: Date; toUtc: Date }
): Promise<OrderRow[]> {
  const where: Prisma.ordersWhereInput = { business_id: businessId }
  if (range) {
    where.created_at = { gte: range.fromUtc, lte: range.toUtc }
  }
  const orders = await prisma.orders.findMany({
    where,
    orderBy: { created_at: "desc" },
    include: {
      order_items: {
        include: { products: true },
      },
      customers: true,
    },
  })

  return orders.map((order) => {
    const totalAmount = Number(order.total_amount.toString())
    const items = order.order_items.map((oi) => ({
      id: oi.id,
      quantity: oi.quantity,
      productName: oi.products.name,
      notes: oi.notes ?? null,
      unitPrice: Number(oi.unit_price.toString()),
      lineTotal: Number(oi.line_total.toString()),
    }))
    const subtotal = items.reduce((sum, it) => sum + it.lineTotal, 0)
    // Orders only persist total_amount = subtotal + delivery_fee. Reverse-
    // engineer the fee for display; clamp to 0 to defend against legacy rows.
    const deliveryFee = Math.max(0, totalAmount - subtotal)
    return {
      id: order.id,
      created_at: order.created_at ? order.created_at.toISOString() : null,
      whatsapp_id: order.whatsapp_id ?? null,
      customer_id: order.customer_id ?? null,
      customer_name: order.customers?.name ?? null,
      delivery_address: order.delivery_address ?? order.customers?.address ?? null,
      payment_method: order.payment_method ?? order.customers?.payment_method ?? null,
      total_amount: totalAmount,
      subtotal,
      delivery_fee: deliveryFee,
      status: order.status ?? "pending",
      items,
    }
  })
}
