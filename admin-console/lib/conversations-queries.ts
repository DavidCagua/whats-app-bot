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
}

/**
 * Get grouped conversations (one row per whatsapp_id + business_id)
 * with permission-based filtering
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
  // Build where clause
  const whereClause: Prisma.conversationsWhereInput = {}

  // Apply business filter
  if (businessIds !== "all") {
    whereClause.business_id = { in: businessIds }
  }

  // Apply specific business filter (from dropdown)
  if (businessFilter) {
    whereClause.business_id = businessFilter
  }

  // Apply date range filter
  if (dateFrom || dateTo) {
    whereClause.timestamp = {}
    if (dateFrom) whereClause.timestamp.gte = dateFrom
    if (dateTo) whereClause.timestamp.lte = dateTo
  }

  // Apply search filter
  if (searchQuery) {
    whereClause.OR = [
      { whatsapp_id: { contains: searchQuery } },
      { message: { contains: searchQuery, mode: "insensitive" } },
    ]
  }

  // Get grouped conversations
  const groupedConversations = await prisma.conversations.groupBy({
    by: ["whatsapp_id", "business_id"],
    where: whereClause,
    _count: { id: true },
    _max: {
      timestamp: true,
      message: true,
    },
    orderBy: {
      _max: { timestamp: "desc" },
    },
    take: limit,
    skip: offset,
  })

  // Get business and customer data for enrichment
  const businessIds_to_fetch = [
    ...new Set(groupedConversations.map((c) => c.business_id)),
  ]
  const whatsappIds_to_fetch = [
    ...new Set(groupedConversations.map((c) => c.whatsapp_id)),
  ]

  const [businesses, customers, whatsappNumbers] = await Promise.all([
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
  ])

  // Create lookup maps
  const businessMap = new Map(businesses.map((b) => [b.id, b.name]))
  const customerMap = new Map(customers.map((c) => [c.whatsapp_id, c.name]))
  const whatsappNumberMap = new Map(
    whatsappNumbers.map((w) => [w.business_id, w.phone_number])
  )

  // Enrich and return
  return groupedConversations.map((conv) => ({
    whatsapp_id: conv.whatsapp_id,
    business_id: conv.business_id,
    business_name: businessMap.get(conv.business_id) || "Unknown Business",
    customer_name: customerMap.get(conv.whatsapp_id) || null,
    last_message: conv._max.message || "",
    last_timestamp: conv._max.timestamp || new Date(),
    message_count: conv._count.id,
    whatsapp_number: whatsappNumberMap.get(conv.business_id) || null,
  }))
}

/**
 * Get full conversation thread for a specific whatsapp_id and business
 */
export async function getConversationThread({
  whatsappId,
  businessId,
}: {
  whatsappId: string
  businessId: string
}): Promise<ConversationThread | null> {
  // Get all messages for this conversation (include whatsapp_number_id and attachments)
  const rawMessages = await prisma.conversations.findMany({
    where: {
      whatsapp_id: whatsappId,
      business_id: businessId,
    },
    orderBy: { timestamp: "asc" },
    include: {
      conversation_attachments: true,
    },
  })

  if (rawMessages.length === 0) {
    return null
  }

  const messages = rawMessages.map((m) => ({
    id: m.id,
    whatsapp_id: m.whatsapp_id,
    message: m.message,
    role: m.role,
    timestamp: m.timestamp,
    created_at: m.created_at,
    attachments: (m.conversation_attachments ?? []).map((a) => ({
      id: a.id,
      type: a.type,
      url: a.url,
      content_type: a.content_type,
      duration_sec: a.duration_sec != null ? Number(a.duration_sec) : null,
      transcript: a.transcript,
    })),
  }))

  // Resolve channel (phone_number_id and/or phone_number) so send uses same number as routing
  const firstWithNumber = rawMessages.find((m) => m.whatsapp_number_id != null)
  let phone_number_id: string | null = null
  let phone_number: string | null = null
  if (firstWithNumber?.whatsapp_number_id) {
    const wn = await prisma.whatsapp_numbers.findUnique({
      where: { id: firstWithNumber.whatsapp_number_id },
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
    select: { agent_enabled: true },
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
    total_messages: messages.length,
    phone_number_id,
    phone_number,
    agent_enabled: agentRow?.agent_enabled ?? true,
  }
}

/**
 * Get conversation statistics
 */
export async function getConversationStats({
  businessIds,
}: {
  businessIds: string[] | "all"
}) {
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

  return {
    totalMessages,
    uniqueCustomers,
    todayMessages,
  }
}
