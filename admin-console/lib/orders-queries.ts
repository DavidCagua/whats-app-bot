import { prisma } from "./prisma"
import type { Prisma } from "@prisma/client"

export type FulfillmentType = "delivery" | "pickup"

export type OrderRow = {
  id: string
  display_number: number
  display_date: string
  created_at: string | null
  whatsapp_id: string | null
  customer_id: number | null
  customer_name: string | null
  delivery_address: string | null
  payment_method: string | null
  total_amount: number
  subtotal: number
  delivery_fee: number
  fulfillment_type: FulfillmentType
  notes: string | null
  status: string
  /** True when this order's conversation has been auto-handed off to
   * a human (e.g. customer asked for status >50min after placement). */
  awaiting_handoff: boolean
  /** True when an operator edited this order via the admin UI and no
   * one has clicked "Marcar revisado" since. Cleared by acknowledge
   * action or by the order reaching a terminal status. */
  hasUnackedEdit: boolean
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

  // One round-trip to fetch the set of whatsapp_ids currently in
  // handoff for this business. Avoids per-order N+1 queries — the
  // orders table can have hundreds of rows.
  const handoffWaIds = new Set(
    (
      await prisma.conversation_agent_settings.findMany({
        where: { business_id: businessId, handoff_reason: { not: null } },
        select: { whatsapp_id: true },
      })
    ).map((r) => r.whatsapp_id)
  )

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
    const status = order.status ?? "pending"
    // hasUnackedEdit: an operator edit is outstanding when the edit
    // marker is set, the ack is missing or older, and the order is
    // still active. Terminal orders never show the warning.
    const isTerminal = status === "completed" || status === "cancelled"
    const hasUnackedEdit =
      !isTerminal &&
      order.last_edited_at !== null &&
      (order.last_edit_acknowledged_at === null ||
        order.last_edit_acknowledged_at < order.last_edited_at)
    return {
      id: order.id,
      display_number: order.display_number,
      display_date: order.display_date.toISOString().slice(0, 10),
      created_at: order.created_at ? order.created_at.toISOString() : null,
      whatsapp_id: order.whatsapp_id ?? null,
      customer_id: order.customer_id ?? null,
      customer_name: order.customers?.name ?? null,
      delivery_address: order.delivery_address ?? order.customers?.address ?? null,
      payment_method: order.payment_method ?? order.customers?.payment_method ?? null,
      total_amount: totalAmount,
      subtotal,
      delivery_fee: deliveryFee,
      fulfillment_type: order.fulfillment_type === "pickup" ? "pickup" : "delivery",
      notes: order.notes ?? null,
      status,
      awaiting_handoff: order.whatsapp_id ? handoffWaIds.has(order.whatsapp_id) : false,
      hasUnackedEdit,
      items,
    }
  })
}

export type OrderBannerCounts = {
  /** Orders still awaiting merchant confirmation. */
  pending: number
  /** Confirmed orders that haven't been delivered yet (confirmed + out_for_delivery). */
  inFlight: number
  /** Conversations the bot auto-handed off to a human (e.g. delivery follow-up). */
  awaitingHandoff: number
  /** Active orders an operator edited and that no one has yet acknowledged. */
  unreviewedEdits: number
}

export async function getOrderBannerCounts(
  businessId: string
): Promise<OrderBannerCounts> {
  const [pending, inFlight, awaitingHandoff, unreviewedEdits] = await Promise.all([
    prisma.orders.count({
      where: { business_id: businessId, status: "pending" },
    }),
    prisma.orders.count({
      where: {
        business_id: businessId,
        status: { in: ["confirmed", "out_for_delivery", "ready_for_pickup"] },
      },
    }),
    prisma.conversation_agent_settings.count({
      where: { business_id: businessId, handoff_reason: { not: null } },
    }),
    // Unreviewed-edits count. Mirrors the `hasUnackedEdit` flag the
    // row mapper computes per order, but expressed in SQL: edited at
    // least once, no ack since the latest edit, and still active.
    // Indexed by idx_orders_unacked_edits (partial index in Alembic
    // migration q2l4m7n9o1k5_orders_edit_review). Prisma's typed
    // filter API can't express column-vs-column inequality, so we
    // drop to raw SQL — single round-trip and the partial index makes
    // it cheap.
    prisma.$queryRaw<{ count: bigint }[]>`
      SELECT COUNT(*)::bigint AS count
      FROM orders
      WHERE business_id = ${businessId}::uuid
        AND status NOT IN ('completed', 'cancelled')
        AND last_edited_at IS NOT NULL
        AND (
          last_edit_acknowledged_at IS NULL
          OR last_edit_acknowledged_at < last_edited_at
        )
    `.then((rows) => Number(rows[0]?.count ?? 0)),
  ])
  return { pending, inFlight, awaitingHandoff, unreviewedEdits }
}
