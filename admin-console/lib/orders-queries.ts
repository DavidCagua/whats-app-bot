import { prisma } from "./prisma"

export type OrderRow = {
  id: string
  created_at: string | null
  whatsapp_id: string | null
  customer_id: number | null
  total_amount: number
  status: string
  items: {
    id: string
    quantity: number
    productName: string
    notes: string | null
  }[]
}

/**
 * List orders for a business in newest-first order with their items.
 * Shared by the orders RSC and the SSE snapshot so both produce
 * identical row shapes.
 */
export async function getOrdersForBusiness(businessId: string): Promise<OrderRow[]> {
  const orders = await prisma.orders.findMany({
    where: { business_id: businessId },
    orderBy: { created_at: "desc" },
    include: {
      order_items: {
        include: { products: true },
      },
    },
  })

  return orders.map((order) => ({
    id: order.id,
    created_at: order.created_at ? order.created_at.toISOString() : null,
    whatsapp_id: order.whatsapp_id ?? null,
    customer_id: order.customer_id ?? null,
    total_amount: Number(order.total_amount.toString()),
    status: order.status ?? "pending",
    items: order.order_items.map((oi) => ({
      id: oi.id,
      quantity: oi.quantity,
      productName: oi.products.name,
      notes: oi.notes ?? null,
    })),
  }))
}
