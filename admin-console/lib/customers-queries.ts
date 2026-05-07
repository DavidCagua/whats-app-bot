import { prisma } from "./prisma"

export type CustomerRow = {
  id: number
  whatsapp_id: string
  name: string
  phone: string | null
  address: string | null
  payment_method: string | null
  source: string
  orders_count: number
  last_seen_at: string | null
  created_at: string
}

/**
 * Returns customers linked to the given business via the
 * `business_customers` join table, sorted by most recent activity.
 *
 * Per-business overrides on the join row (name, phone, address,
 * payment_method) win over the global `customers` row when present.
 *
 * `last_seen_at` is the latest `orders.created_at` for this business
 * (bookings deliberately not included so the column reads cleanly for
 * order-only businesses like Biela; revisit if booking businesses need
 * it surfaced too).
 */
export async function getCustomersForBusiness(
  businessId: string
): Promise<CustomerRow[]> {
  const links = await prisma.business_customers.findMany({
    where: { business_id: businessId },
    include: {
      customers: {
        include: {
          _count: {
            select: { orders: { where: { business_id: businessId } } },
          },
          orders: {
            where: { business_id: businessId },
            orderBy: { created_at: "desc" },
            take: 1,
            select: { created_at: true },
          },
        },
      },
    },
    orderBy: { updated_at: "desc" },
  })

  const rows = links.map<CustomerRow>((link) => {
    const c = link.customers
    const lastOrder = c.orders[0]?.created_at ?? null
    return {
      id: c.id,
      whatsapp_id: c.whatsapp_id,
      name: link.name ?? c.name,
      phone: link.phone ?? c.phone,
      address: link.address ?? c.address,
      payment_method: link.payment_method ?? c.payment_method,
      source: link.source,
      orders_count: c._count.orders,
      last_seen_at: lastOrder ? lastOrder.toISOString() : null,
      created_at: link.created_at.toISOString(),
    }
  })

  rows.sort((a, b) => {
    const av = a.last_seen_at ?? a.created_at
    const bv = b.last_seen_at ?? b.created_at
    return bv.localeCompare(av)
  })

  return rows
}
