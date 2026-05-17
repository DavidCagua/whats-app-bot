import { unstable_cache } from "next/cache"
import { prisma } from "./prisma"
import { Prisma } from "@prisma/client"

export type ConversationGroup = {
  whatsapp_id: string
  business_id: string
  business_name: string
  customer_name: string | null
  last_message: string
  last_timestamp: Date
  message_count: number
  whatsapp_number: string | null
  /** Reason the bot is currently disabled, or null if it's active.
   * Auto-set by the customer-service flow:
   *   "delivery_handoff" — 50-min order-status threshold tripped.
   *   "payment_proof"   — customer sent a receipt image/PDF. */
  handoff_reason: string | null
}

export type ConversationMessageAttachment = {
  id: string
  type: string
  url: string | null
  content_type: string | null
  duration_sec: number | null
  transcript: string | null
}

export type ConversationMessage = {
  id: number
  whatsapp_id: string
  message: string
  role: string
  timestamp: Date
  created_at: Date
  attachments?: ConversationMessageAttachment[]
}

export type ConversationThread = {
  whatsapp_id: string
  business_id: string
  business_name: string
  customer_name: string | null
  customer_phone: string
  messages: ConversationMessage[]
  total_messages: number
  /** Meta phone_number_id (or twilio:...) for the channel; use when sending so routing matches. */
  phone_number_id: string | null
  /** E.164 phone number for the channel when phone_number_id is null; use for send lookup. */
  phone_number: string | null
  /** Conversation-level agent enable flag (defaults to true). */
  agent_enabled: boolean
  /** Reason the bot is currently disabled (e.g. "delivery_handoff"); null when enabled. */
  handoff_reason: string | null
}

/**
 * Get grouped conversations (one row per whatsapp_id + business_id)
 * with permission-based filtering. Uses Postgres DISTINCT ON to fetch
 * the actual latest message per group — Prisma's `_max: { message }`
 * returns the lexicographically max string, not the message at the
 * latest timestamp, which surfaces as random old messages in the list.
 */
export async function getConversations({
  businessIds,
  businessFilter,
  searchQuery,
  dateFrom,
  dateTo,
  limit = 50,
  offset = 0,
}: {
  businessIds: string[] | "all"
  businessFilter?: string
  searchQuery?: string
  dateFrom?: Date
  dateTo?: Date
  limit?: number
  offset?: number
}): Promise<ConversationGroup[]> {
  const whereParts: Prisma.Sql[] = []

  if (businessIds !== "all") {
    if (businessIds.length === 0) return []
    whereParts.push(Prisma.sql`business_id = ANY(${businessIds}::uuid[])`)
  }
  if (businessFilter) {
    whereParts.push(Prisma.sql`business_id = ${businessFilter}::uuid`)
  }
  if (searchQuery) {
    const pattern = `%${searchQuery}%`
    whereParts.push(
      Prisma.sql`(whatsapp_id ILIKE ${pattern} OR message ILIKE ${pattern})`
    )
  }
  if (dateFrom) {
    whereParts.push(Prisma.sql`timestamp >= ${dateFrom}`)
  }
  if (dateTo) {
    whereParts.push(Prisma.sql`timestamp <= ${dateTo}`)
  }

  const whereClause =
    whereParts.length > 0
      ? Prisma.sql`WHERE ${Prisma.join(whereParts, " AND ")}`
      : Prisma.empty

  const rows = await prisma.$queryRaw<
    Array<{
      whatsapp_id: string
      business_id: string
      last_message: string
      last_timestamp: Date
      message_count: bigint
    }>
  >`
    WITH filtered AS (
      SELECT whatsapp_id, business_id, message, timestamp
      FROM conversations
      ${whereClause}
    ),
    latest AS (
      SELECT DISTINCT ON (whatsapp_id, business_id)
        whatsapp_id,
        business_id,
        message AS last_message,
        timestamp AS last_timestamp
      FROM filtered
      ORDER BY whatsapp_id, business_id, timestamp DESC
    ),
    counts AS (
      SELECT whatsapp_id, business_id, COUNT(*) AS message_count
      FROM filtered
      GROUP BY whatsapp_id, business_id
    )
    SELECT l.whatsapp_id,
           l.business_id,
           l.last_message,
           l.last_timestamp,
           c.message_count
    FROM latest l
    JOIN counts c USING (whatsapp_id, business_id)
    ORDER BY l.last_timestamp DESC
    LIMIT ${limit} OFFSET ${offset}
  `

  if (rows.length === 0) return []

  const businessIds_to_fetch = [...new Set(rows.map((r) => r.business_id))]
  const whatsappIds_to_fetch = [...new Set(rows.map((r) => r.whatsapp_id))]

  const [businesses, customers, whatsappNumbers, handoffRows] = await Promise.all([
    prisma.businesses.findMany({
      where: { id: { in: businessIds_to_fetch } },
      select: { id: true, name: true },
    }),
    prisma.customers.findMany({
      where: { whatsapp_id: { in: whatsappIds_to_fetch } },
      select: { whatsapp_id: true, name: true },
    }),
    prisma.whatsapp_numbers.findMany({
      where: { business_id: { in: businessIds_to_fetch } },
      select: { business_id: true, phone_number: true },
    }),
    prisma.conversation_agent_settings.findMany({
      where: {
        business_id: { in: businessIds_to_fetch },
        whatsapp_id: { in: whatsappIds_to_fetch },
        handoff_reason: { not: null },
      },
      select: { business_id: true, whatsapp_id: true, handoff_reason: true },
    }),
  ])

  const businessMap = new Map(businesses.map((b) => [b.id, b.name]))
  const customerMap = new Map(customers.map((c) => [c.whatsapp_id, c.name]))
  const whatsappNumberMap = new Map(
    whatsappNumbers.map((w) => [w.business_id, w.phone_number])
  )
  // Composite key (business_id|whatsapp_id) → reason. Filtered to non-null
  // reasons so a "manual disable" doesn't decorate the row in the UI —
  // those convos still appear normal until they auto-escalate.
  const handoffKey = (b: string, w: string) => `${b}|${w}`
  const handoffMap = new Map(
    handoffRows.map((h) => [handoffKey(h.business_id, h.whatsapp_id), h.handoff_reason])
  )

  return rows.map((r) => ({
    whatsapp_id: r.whatsapp_id,
    business_id: r.business_id,
    business_name: businessMap.get(r.business_id) || "Unknown Business",
    customer_name: customerMap.get(r.whatsapp_id) || null,
    last_message: r.last_message ?? "",
    last_timestamp: r.last_timestamp,
    message_count: Number(r.message_count),
    whatsapp_number: whatsappNumberMap.get(r.business_id) || null,
    handoff_reason: handoffMap.get(handoffKey(r.business_id, r.whatsapp_id)) ?? null,
  }))
}

/**
 * Cursor-paginated conversation thread. Default returns the latest
 * `limit` messages newest-first reversed to chronological order; when
 * `before` is set, returns the next page of messages with id < before.
 *
 * `total_messages` is the grand total count for the conversation, not
 * the size of the returned slice — the client uses it to compute
 * `hasMoreOlder = localMessages.length < total_messages`.
 */
export async function getConversationThread({
  whatsappId,
  businessId,
  limit = 50,
  before,
}: {
  whatsappId: string
  businessId: string
  limit?: number
  before?: number
}): Promise<ConversationThread | null> {
  const messageWhere: Prisma.conversationsWhereInput = {
    whatsapp_id: whatsappId,
    business_id: businessId,
  }
  if (before !== undefined) {
    messageWhere.id = { lt: before }
  }

  const [rawMessagesDesc, totalMessages] = await Promise.all([
    prisma.conversations.findMany({
      where: messageWhere,
      orderBy: { id: "desc" },
      take: limit,
      select: {
        id: true,
        whatsapp_id: true,
        message: true,
        role: true,
        timestamp: true,
        created_at: true,
        whatsapp_number_id: true,
      },
    }),
    prisma.conversations.count({
      where: { whatsapp_id: whatsappId, business_id: businessId },
    }),
  ])

  if (totalMessages === 0) {
    return null
  }

  const rawMessages = rawMessagesDesc.slice().reverse()

  const conversationIds = rawMessages.map((m) => m.id)
  const attachments = conversationIds.length
    ? await prisma.conversation_attachments.findMany({
        where: { conversation_id: { in: conversationIds } },
      })
    : []
  const attachmentsByConversationId = new Map<number, typeof attachments>()
  for (const a of attachments) {
    const list = attachmentsByConversationId.get(a.conversation_id) ?? []
    list.push(a)
    attachmentsByConversationId.set(a.conversation_id, list)
  }

  const messages = rawMessages.map((m) => ({
    id: m.id,
    whatsapp_id: m.whatsapp_id,
    message: m.message,
    role: m.role,
    timestamp: m.timestamp,
    created_at: m.created_at,
    attachments: (attachmentsByConversationId.get(m.id) ?? []).map((a) => ({
      id: a.id,
      type: a.type,
      url: a.url,
      content_type: a.content_type,
      duration_sec: a.duration_sec != null ? Number(a.duration_sec) : null,
      transcript: a.transcript,
    })),
  }))

  // Channel resolution must look across the whole conversation so the
  // answer is stable regardless of which page is loaded.
  const channelMsg = await prisma.conversations.findFirst({
    where: {
      whatsapp_id: whatsappId,
      business_id: businessId,
      whatsapp_number_id: { not: null },
    },
    orderBy: { id: "asc" },
    select: { whatsapp_number_id: true },
  })

  let phone_number_id: string | null = null
  let phone_number: string | null = null
  if (channelMsg?.whatsapp_number_id) {
    const wn = await prisma.whatsapp_numbers.findUnique({
      where: { id: channelMsg.whatsapp_number_id },
      select: { phone_number_id: true, phone_number: true },
    })
    phone_number_id = wn?.phone_number_id ?? null
    phone_number = wn?.phone_number ?? null
  }
  if (phone_number_id == null && phone_number == null) {
    const fallback = await prisma.whatsapp_numbers.findFirst({
      where: { business_id: businessId, is_active: true },
      select: { phone_number_id: true, phone_number: true },
    })
    phone_number_id = fallback?.phone_number_id ?? null
    phone_number = fallback?.phone_number ?? null
  }

  const agentRow = await prisma.conversation_agent_settings.findFirst({
    where: { business_id: businessId, whatsapp_id: whatsappId },
    orderBy: { updated_at: "desc" },
    select: { agent_enabled: true, handoff_reason: true },
  })

  const [business, customer] = await Promise.all([
    prisma.businesses.findUnique({
      where: { id: businessId },
      select: { name: true },
    }),
    prisma.customers.findUnique({
      where: { whatsapp_id: whatsappId },
      select: { name: true },
    }),
  ])

  return {
    whatsapp_id: whatsappId,
    business_id: businessId,
    business_name: business?.name || "Unknown Business",
    customer_name: customer?.name || null,
    customer_phone: whatsappId,
    messages,
    total_messages: totalMessages,
    phone_number_id,
    phone_number,
    agent_enabled: agentRow?.agent_enabled ?? true,
    handoff_reason: agentRow?.handoff_reason ?? null,
  }
}

/**
 * Get conversation statistics. Wrapped in unstable_cache so the slow
 * `groupBy` for unique-customer count doesn't gate the inbox RSC paint
 * on every navigation; only revalidates once per minute. The header
 * stats can tolerate a 60s lag without affecting message correctness.
 */
async function _getConversationStats(
  businessIdsKey: string
): Promise<{ totalMessages: number; uniqueCustomers: number; todayMessages: number }> {
  const businessIds: string[] | "all" =
    businessIdsKey === "all" ? "all" : (JSON.parse(businessIdsKey) as string[])

  const whereClause: Prisma.conversationsWhereInput =
    businessIds !== "all" ? { business_id: { in: businessIds } } : {}

  const [totalMessages, uniqueCustomers, todayMessages] = await Promise.all([
    prisma.conversations.count({ where: whereClause }),
    prisma.conversations
      .groupBy({
        by: ["whatsapp_id"],
        where: whereClause,
      })
      .then((result) => result.length),
    prisma.conversations.count({
      where: {
        ...whereClause,
        timestamp: {
          gte: new Date(new Date().setHours(0, 0, 0, 0)),
        },
      },
    }),
  ])

  return { totalMessages, uniqueCustomers, todayMessages }
}

const _cachedConversationStats = unstable_cache(
  _getConversationStats,
  ["conversation-stats"],
  { revalidate: 60, tags: ["conversation-stats"] }
)

export async function getConversationStats({
  businessIds,
}: {
  businessIds: string[] | "all"
}) {
  // Stable key: sorted JSON for arrays so [a,b] and [b,a] hit the same entry.
  const key =
    businessIds === "all" ? "all" : JSON.stringify([...businessIds].sort())
  return _cachedConversationStats(key)
}
