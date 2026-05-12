import { prisma } from "@/lib/prisma"
import { Prisma } from "@prisma/client"
import type { DateRange } from "@/lib/orders-date-range"
import { rangeToUtc } from "@/lib/orders-date-range"

// Constants the dashboard treats as fixed for every business today.
// When we eventually persist them per business (orders.delivery_fee,
// businesses.settings.sla_prep_minutes) the dashboard will read those
// values instead — these are the single source of truth in the meantime.
export const DELIVERY_FEE_COP = 7000
export const DEMORA_THRESHOLD_MIN = 50
// Bot turns longer than this are almost certainly a human handoff or
// an outage, not bot latency. Cap them so one bad day doesn't poison
// the average.
const BOT_RESPONSE_CAP_SEC = 300

const TOP_PRODUCTS_LIMIT = 5

// Anything matching this regex on `payment_method` (case-insensitive)
// counts as cash. Stored values are user-typed strings, so we accept a
// few common variants.
const CASH_PAYMENT_REGEX = "^(efectivo|cash|contado|plata)"

export type DashboardKpis = {
  summary: {
    uniqueChats: number
    chatsWithOrders: number
    orders: number
    conversionPct: number | null
    ordersBot: number
    ordersAdmin: number
    incompletePct: number | null
    cancelledByBusiness: number
    cancelledByCustomer: number
    cancelledOther: number
    avgTicketNoDelivery: number | null
    cashRevenue: number
    promosCount: number
    discountPctOfRevenue: number | null
  }
  performance: {
    avgPrepMin: number | null
    avgDispatchMin: number | null
    avgTotalDeliveryMin: number | null
    delayedCount: number
    avgBotResponseSec: number | null
    avgTimeToOrderMin: number | null
  }
  catalog: {
    topProducts: { name: string; qty: number }[]
    recurringCustomers: number
  }
  constants: {
    deliveryFeeCop: number
    demoraThresholdMin: number
  }
}

type SummaryRow = {
  unique_chats: bigint
  chats_with_orders: bigint
  orders: bigint
  orders_bot: bigint
  orders_admin: bigint
  incomplete: bigint
  cancelled_by_business: bigint
  cancelled_by_customer: bigint
  cancelled_other: bigint
  avg_ticket_no_delivery: number | null
  cash_revenue: number | null
  promos_count: bigint
  discount_total: number | null
  revenue_total: number | null
}

type PerformanceRow = {
  avg_prep_sec: number | null
  avg_dispatch_sec: number | null
  avg_total_sec: number | null
  delayed_count: bigint
  avg_bot_response_sec: number | null
  avg_time_to_order_sec: number | null
}

type TopProductRow = { name: string; qty: bigint }
type RecurringRow = { count: bigint }

function toNumber(v: bigint | number | null | undefined): number {
  if (v === null || v === undefined) return 0
  return typeof v === "bigint" ? Number(v) : v
}

function pct(numerator: number, denominator: number): number | null {
  if (!denominator) return null
  return (numerator / denominator) * 100
}

function secsToMin(s: number | null | undefined): number | null {
  if (s === null || s === undefined) return null
  return s / 60
}

/**
 * Fetch every KPI the dashboard shows for the given Bogotá-local range,
 * in a single business hop.
 *
 * Four independent queries run in parallel against the same range:
 *   1. Summary — counts, sums, mix, promos.
 *   2. Performance — prep/dispatch/total times, demora count, bot
 *      response time, time-to-order.
 *   3. Top products — order-item rollup.
 *   4. Recurring customers — customer-grouped order count.
 *
 * Time math is done in Postgres with `AT TIME ZONE 'America/Bogota'`
 * for the conversations filter, and the orders filter rides on
 * `display_date` (a Bogotá-local Date column) so day boundaries don't
 * drift if the server clock is UTC.
 *
 * The analytics columns added in alembic p1k3l6m8n0j4 (created_via,
 * cancelled_by, out_for_delivery_at) are populated from that cutover
 * forward — pre-migration orders show up as `created_via='bot'`
 * (DB default) and NULL `cancelled_by`, which is the right behaviour
 * for historical bot-only traffic.
 */
export async function getDashboardKpis(
  businessId: string,
  range: DateRange
): Promise<DashboardKpis> {
  const { fromUtc, toUtc } = rangeToUtc(range)
  const fromDate = range.from
  const toDate = range.to

  const [summaryRows, performanceRows, topProductRows, recurringRows] =
    await Promise.all([
      prisma.$queryRaw<SummaryRow[]>(Prisma.sql`
        WITH range_orders AS (
          SELECT *
          FROM orders
          WHERE business_id = ${businessId}::uuid
            AND display_date BETWEEN ${fromDate}::date AND ${toDate}::date
        ),
        range_chat_ids AS (
          -- Distinct chats in the range. conversations.timestamp is
          -- timestamp WITHOUT time zone in production despite the
          -- Prisma schema declaring tstz — SQLAlchemy persists
          -- datetime.now(timezone.utc) but the column drops the offset.
          -- Tag the value as UTC first, then convert to Bogotá. A plain
          -- AT TIME ZONE America/Bogota on a naive column would do the
          -- inverse (treat as Bogotá local), shifting the result by 5h
          -- in the wrong direction.
          SELECT DISTINCT whatsapp_id
          FROM conversations
          WHERE business_id = ${businessId}::uuid
            AND ((timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'America/Bogota')::date
                BETWEEN ${fromDate}::date AND ${toDate}::date
        ),
        range_promos AS (
          SELECT COUNT(*)::bigint AS promos_count
          FROM order_promotions op
          JOIN range_orders o ON o.id = op.order_id
        )
        SELECT
          (SELECT COUNT(*)::bigint FROM range_chat_ids) AS unique_chats,
          -- Standard conversion: chats that placed at least one order
          -- in the same range. Caps at 100% no matter how many orders
          -- a single chat produced. Admin-created orders (no matching
          -- whatsapp_id in conversations) are correctly excluded from
          -- the numerator.
          (SELECT COUNT(*)::bigint FROM range_chat_ids c
             WHERE EXISTS (
               SELECT 1 FROM range_orders o
               WHERE o.whatsapp_id = c.whatsapp_id
             )) AS chats_with_orders,
          (SELECT COUNT(*)::bigint FROM range_orders) AS orders,
          (SELECT COUNT(*)::bigint FROM range_orders WHERE created_via = 'bot') AS orders_bot,
          (SELECT COUNT(*)::bigint FROM range_orders WHERE created_via <> 'bot') AS orders_admin,
          (SELECT COUNT(*)::bigint FROM range_orders
             WHERE status NOT IN ('completed', 'cancelled')) AS incomplete,
          (SELECT COUNT(*)::bigint FROM range_orders
             WHERE status = 'cancelled' AND cancelled_by = 'business') AS cancelled_by_business,
          (SELECT COUNT(*)::bigint FROM range_orders
             WHERE status = 'cancelled' AND cancelled_by = 'customer') AS cancelled_by_customer,
          (SELECT COUNT(*)::bigint FROM range_orders
             WHERE status = 'cancelled'
               AND (cancelled_by IS NULL OR cancelled_by NOT IN ('business', 'customer'))) AS cancelled_other,
          (SELECT AVG(
             CASE WHEN fulfillment_type = 'delivery'
                  THEN GREATEST(total_amount - ${DELIVERY_FEE_COP}, 0)
                  ELSE total_amount END
           )::float8 FROM range_orders
           WHERE status NOT IN ('cancelled')) AS avg_ticket_no_delivery,
          (SELECT COALESCE(SUM(total_amount), 0)::float8 FROM range_orders
            WHERE status = 'completed'
              AND payment_method IS NOT NULL
              AND payment_method ~* ${CASH_PAYMENT_REGEX}) AS cash_revenue,
          (SELECT promos_count FROM range_promos) AS promos_count,
          (SELECT COALESCE(SUM(promo_discount_amount), 0)::float8 FROM range_orders
            WHERE status NOT IN ('cancelled')) AS discount_total,
          (SELECT COALESCE(SUM(total_amount), 0)::float8 FROM range_orders
            WHERE status NOT IN ('cancelled')) AS revenue_total
      `),
      prisma.$queryRaw<PerformanceRow[]>(Prisma.sql`
        WITH range_orders AS (
          SELECT *
          FROM orders
          WHERE business_id = ${businessId}::uuid
            AND created_at >= ${fromUtc}
            AND created_at <  ${toUtc}
        ),
        prep_times AS (
          -- Preparation = confirmed_at → ready/out_for_delivery, depending
          -- on fulfillment. Pickup uses ready_at; delivery uses
          -- out_for_delivery_at (with completed_at as a fallback so
          -- migration-era rows without out_for_delivery_at still report).
          SELECT
            EXTRACT(EPOCH FROM (
              COALESCE(
                CASE WHEN fulfillment_type = 'pickup' THEN ready_at ELSE out_for_delivery_at END,
                completed_at
              ) - confirmed_at
            )) AS prep_sec
          FROM range_orders
          WHERE confirmed_at IS NOT NULL
            AND (ready_at IS NOT NULL OR out_for_delivery_at IS NOT NULL OR completed_at IS NOT NULL)
        ),
        dispatch_times AS (
          SELECT
            EXTRACT(EPOCH FROM (completed_at - out_for_delivery_at)) AS dispatch_sec
          FROM range_orders
          WHERE fulfillment_type = 'delivery'
            AND out_for_delivery_at IS NOT NULL
            AND completed_at IS NOT NULL
        ),
        total_times AS (
          SELECT EXTRACT(EPOCH FROM (completed_at - confirmed_at)) AS total_sec
          FROM range_orders
          WHERE confirmed_at IS NOT NULL AND completed_at IS NOT NULL
        ),
        bot_pairs AS (
          -- Adjacent user → assistant message pairs in the range.
          -- LEAD() walks the conversation forward so we don't need a
          -- correlated subquery per message row. conversations.timestamp
          -- is naive UTC in production (see range_chats note), so we
          -- tag it as UTC before comparing against the tz-aware range
          -- bounds — and use the tz-aware values for the LEAD math too.
          SELECT EXTRACT(EPOCH FROM (next_ts - ts)) AS response_sec
          FROM (
            SELECT
              role,
              (timestamp AT TIME ZONE 'UTC') AS ts,
              LEAD(role) OVER (
                PARTITION BY business_id, whatsapp_id ORDER BY timestamp
              ) AS next_role,
              LEAD(timestamp AT TIME ZONE 'UTC') OVER (
                PARTITION BY business_id, whatsapp_id ORDER BY timestamp
              ) AS next_ts
            FROM conversations
            WHERE business_id = ${businessId}::uuid
              AND (timestamp AT TIME ZONE 'UTC') >= ${fromUtc}
              AND (timestamp AT TIME ZONE 'UTC') <  ${toUtc}
          ) c
          WHERE role = 'user'
            AND next_role = 'assistant'
            AND next_ts IS NOT NULL
        ),
        time_to_order AS (
          SELECT EXTRACT(EPOCH FROM (o.confirmed_at - cda.first_msg_at)) AS sec
          FROM orders o
          JOIN conversation_daily_analyses cda ON cda.order_id = o.id
          WHERE o.business_id = ${businessId}::uuid
            AND o.confirmed_at IS NOT NULL
            AND cda.first_msg_at IS NOT NULL
            AND o.created_at >= ${fromUtc}
            AND o.created_at <  ${toUtc}
        )
        SELECT
          (SELECT AVG(prep_sec)::float8 FROM prep_times) AS avg_prep_sec,
          (SELECT AVG(dispatch_sec)::float8 FROM dispatch_times) AS avg_dispatch_sec,
          (SELECT AVG(total_sec)::float8 FROM total_times) AS avg_total_sec,
          (SELECT COUNT(*)::bigint FROM prep_times
            WHERE prep_sec > ${DEMORA_THRESHOLD_MIN * 60}) AS delayed_count,
          (SELECT AVG(response_sec)::float8 FROM bot_pairs
            WHERE response_sec BETWEEN 0 AND ${BOT_RESPONSE_CAP_SEC}) AS avg_bot_response_sec,
          (SELECT AVG(sec)::float8 FROM time_to_order WHERE sec > 0) AS avg_time_to_order_sec
      `),
      prisma.$queryRaw<TopProductRow[]>(Prisma.sql`
        SELECT p.name AS name, SUM(oi.quantity)::bigint AS qty
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        WHERE o.business_id = ${businessId}::uuid
          AND o.status NOT IN ('cancelled')
          AND o.display_date BETWEEN ${fromDate}::date AND ${toDate}::date
        GROUP BY p.id, p.name
        ORDER BY qty DESC
        LIMIT ${TOP_PRODUCTS_LIMIT}
      `),
      prisma.$queryRaw<RecurringRow[]>(Prisma.sql`
        SELECT COUNT(*)::bigint AS count
        FROM (
          SELECT customer_id
          FROM orders
          WHERE business_id = ${businessId}::uuid
            AND customer_id IS NOT NULL
            AND status NOT IN ('cancelled')
            AND display_date BETWEEN ${fromDate}::date AND ${toDate}::date
          GROUP BY customer_id
          HAVING COUNT(*) > 1
        ) recurring
      `),
    ])

  const s = summaryRows[0] ?? ({} as SummaryRow)
  const p = performanceRows[0] ?? ({} as PerformanceRow)

  const uniqueChats = toNumber(s.unique_chats)
  const chatsWithOrders = toNumber(s.chats_with_orders)
  const ordersInRange = toNumber(s.orders)
  const revenueTotal = toNumber(s.revenue_total ?? 0)
  const discountTotal = toNumber(s.discount_total ?? 0)

  return {
    summary: {
      uniqueChats,
      chatsWithOrders,
      orders: ordersInRange,
      conversionPct: pct(chatsWithOrders, uniqueChats),
      ordersBot: toNumber(s.orders_bot),
      ordersAdmin: toNumber(s.orders_admin),
      incompletePct: pct(toNumber(s.incomplete), ordersInRange),
      cancelledByBusiness: toNumber(s.cancelled_by_business),
      cancelledByCustomer: toNumber(s.cancelled_by_customer),
      cancelledOther: toNumber(s.cancelled_other),
      avgTicketNoDelivery: s.avg_ticket_no_delivery ?? null,
      cashRevenue: toNumber(s.cash_revenue ?? 0),
      promosCount: toNumber(s.promos_count),
      discountPctOfRevenue: pct(discountTotal, revenueTotal),
    },
    performance: {
      avgPrepMin: secsToMin(p.avg_prep_sec),
      avgDispatchMin: secsToMin(p.avg_dispatch_sec),
      avgTotalDeliveryMin: secsToMin(p.avg_total_sec),
      delayedCount: toNumber(p.delayed_count),
      avgBotResponseSec: p.avg_bot_response_sec ?? null,
      avgTimeToOrderMin: secsToMin(p.avg_time_to_order_sec),
    },
    catalog: {
      topProducts: topProductRows.map((r) => ({
        name: r.name,
        qty: toNumber(r.qty),
      })),
      recurringCustomers: toNumber(recurringRows[0]?.count ?? 0),
    },
    constants: {
      deliveryFeeCop: DELIVERY_FEE_COP,
      demoraThresholdMin: DEMORA_THRESHOLD_MIN,
    },
  }
}
