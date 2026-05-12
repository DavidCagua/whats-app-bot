import { prisma } from "@/lib/prisma"
import { Prisma } from "@prisma/client"

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

const RECURRING_WINDOW_DAYS = 30
const PERFORMANCE_WINDOW_DAYS = 7
const TOP_PRODUCTS_LIMIT = 5

// Anything matching this regex on `payment_method` (case-insensitive)
// counts as cash. Stored values are user-typed strings, so we accept a
// few common variants.
const CASH_PAYMENT_REGEX = "^(efectivo|cash|contado|plata)"

export type DashboardKpis = {
  today: {
    uniqueChats: number
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
    recurringWindowDays: number
    performanceWindowDays: number
  }
}

type TodayRow = {
  unique_chats: bigint
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
 * Fetch every KPI the dashboard shows in a single business hop.
 *
 * Three independent queries run in parallel:
 *   1. Today's snapshot (Bogotá-local day) — counts, sums, mix.
 *   2. Performance window (last 7 days completed orders + last 7 days
 *      bot conversation latency).
 *   3. Catalog window (last 30 days top products + recurring customers).
 *
 * Time math is done in Postgres with `AT TIME ZONE 'America/Bogota'`
 * so day boundaries don't drift if the server clock is UTC.
 *
 * The new analytics columns (created_via, cancelled_by,
 * out_for_delivery_at) are populated from the migration cutover
 * forward — pre-migration orders show up as `created_via='bot'`
 * (DB default) and NULL cancelled_by, which is the right behaviour
 * for historical-but-bot-only traffic.
 */
export async function getDashboardKpis(
  businessId: string
): Promise<DashboardKpis> {
  const [todayRows, performanceRows, topProductRows, recurringRows] =
    await Promise.all([
      prisma.$queryRaw<TodayRow[]>(Prisma.sql`
        WITH today AS (
          SELECT (now() AT TIME ZONE 'America/Bogota')::date AS d
        ),
        orders_today AS (
          SELECT *
          FROM orders, today
          WHERE business_id = ${businessId}::uuid
            AND display_date = today.d
        ),
        chats_today AS (
          SELECT COUNT(DISTINCT whatsapp_id)::bigint AS unique_chats
          FROM conversations, today
          WHERE business_id = ${businessId}::uuid
            AND (timestamp AT TIME ZONE 'America/Bogota')::date = today.d
        ),
        promos_today AS (
          SELECT COUNT(*)::bigint AS promos_count
          FROM order_promotions op
          JOIN orders_today o ON o.id = op.order_id
        )
        SELECT
          (SELECT unique_chats FROM chats_today) AS unique_chats,
          (SELECT COUNT(*)::bigint FROM orders_today) AS orders,
          (SELECT COUNT(*)::bigint FROM orders_today WHERE created_via = 'bot') AS orders_bot,
          (SELECT COUNT(*)::bigint FROM orders_today WHERE created_via <> 'bot') AS orders_admin,
          (SELECT COUNT(*)::bigint FROM orders_today
             WHERE status NOT IN ('completed', 'cancelled')) AS incomplete,
          (SELECT COUNT(*)::bigint FROM orders_today
             WHERE status = 'cancelled' AND cancelled_by = 'business') AS cancelled_by_business,
          (SELECT COUNT(*)::bigint FROM orders_today
             WHERE status = 'cancelled' AND cancelled_by = 'customer') AS cancelled_by_customer,
          (SELECT COUNT(*)::bigint FROM orders_today
             WHERE status = 'cancelled'
               AND (cancelled_by IS NULL OR cancelled_by NOT IN ('business', 'customer'))) AS cancelled_other,
          (SELECT AVG(
             CASE WHEN fulfillment_type = 'delivery'
                  THEN GREATEST(total_amount - ${DELIVERY_FEE_COP}, 0)
                  ELSE total_amount END
           )::float8 FROM orders_today
           WHERE status NOT IN ('cancelled')) AS avg_ticket_no_delivery,
          (SELECT COALESCE(SUM(total_amount), 0)::float8 FROM orders_today
            WHERE status = 'completed'
              AND payment_method IS NOT NULL
              AND payment_method ~* ${CASH_PAYMENT_REGEX}) AS cash_revenue,
          (SELECT promos_count FROM promos_today) AS promos_count,
          (SELECT COALESCE(SUM(promo_discount_amount), 0)::float8 FROM orders_today
            WHERE status NOT IN ('cancelled')) AS discount_total,
          (SELECT COALESCE(SUM(total_amount), 0)::float8 FROM orders_today
            WHERE status NOT IN ('cancelled')) AS revenue_total
      `),
      prisma.$queryRaw<PerformanceRow[]>(Prisma.sql`
        WITH perf_window AS (
          SELECT now() - INTERVAL '${Prisma.raw(String(PERFORMANCE_WINDOW_DAYS))} days' AS since
        ),
        perf_orders AS (
          SELECT *
          FROM orders, perf_window
          WHERE business_id = ${businessId}::uuid
            AND created_at >= perf_window.since
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
          FROM perf_orders
          WHERE confirmed_at IS NOT NULL
            AND (ready_at IS NOT NULL OR out_for_delivery_at IS NOT NULL OR completed_at IS NOT NULL)
        ),
        dispatch_times AS (
          SELECT
            EXTRACT(EPOCH FROM (completed_at - out_for_delivery_at)) AS dispatch_sec
          FROM perf_orders
          WHERE fulfillment_type = 'delivery'
            AND out_for_delivery_at IS NOT NULL
            AND completed_at IS NOT NULL
        ),
        total_times AS (
          SELECT EXTRACT(EPOCH FROM (completed_at - confirmed_at)) AS total_sec
          FROM perf_orders
          WHERE confirmed_at IS NOT NULL AND completed_at IS NOT NULL
        ),
        bot_pairs AS (
          -- Adjacent user → assistant message pairs in the last window.
          -- LEAD() walks the conversation forward so we don't need a
          -- correlated subquery per message row.
          SELECT EXTRACT(EPOCH FROM (next_ts - ts)) AS response_sec
          FROM (
            SELECT
              role,
              timestamp AS ts,
              LEAD(role) OVER (
                PARTITION BY business_id, whatsapp_id ORDER BY timestamp
              ) AS next_role,
              LEAD(timestamp) OVER (
                PARTITION BY business_id, whatsapp_id ORDER BY timestamp
              ) AS next_ts
            FROM conversations
            WHERE business_id = ${businessId}::uuid
              AND timestamp >= (SELECT since FROM perf_window)
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
            AND o.created_at >= (SELECT since FROM perf_window)
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
          AND o.created_at >= now() - INTERVAL '${Prisma.raw(String(RECURRING_WINDOW_DAYS))} days'
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
            AND created_at >= now() - INTERVAL '${Prisma.raw(String(RECURRING_WINDOW_DAYS))} days'
          GROUP BY customer_id
          HAVING COUNT(*) > 1
        ) recurring
      `),
    ])

  const t = todayRows[0] ?? ({} as TodayRow)
  const p = performanceRows[0] ?? ({} as PerformanceRow)

  const uniqueChats = toNumber(t.unique_chats)
  const ordersToday = toNumber(t.orders)
  const revenueTotal = toNumber(t.revenue_total ?? 0)
  const discountTotal = toNumber(t.discount_total ?? 0)

  return {
    today: {
      uniqueChats,
      orders: ordersToday,
      conversionPct: pct(ordersToday, uniqueChats),
      ordersBot: toNumber(t.orders_bot),
      ordersAdmin: toNumber(t.orders_admin),
      incompletePct: pct(toNumber(t.incomplete), ordersToday),
      cancelledByBusiness: toNumber(t.cancelled_by_business),
      cancelledByCustomer: toNumber(t.cancelled_by_customer),
      cancelledOther: toNumber(t.cancelled_other),
      avgTicketNoDelivery: t.avg_ticket_no_delivery ?? null,
      cashRevenue: toNumber(t.cash_revenue ?? 0),
      promosCount: toNumber(t.promos_count),
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
      recurringWindowDays: RECURRING_WINDOW_DAYS,
      performanceWindowDays: PERFORMANCE_WINDOW_DAYS,
    },
  }
}
