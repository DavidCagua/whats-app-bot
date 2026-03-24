import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { canAccessBusiness } from "@/lib/permissions"
import { getConversationsAccess } from "@/lib/conversations-permissions"
import {
  getConversations,
  getConversationStats,
  getConversationThread,
} from "@/lib/conversations-queries"
import { prisma } from "@/lib/prisma"
import { ConversationsHeader } from "../_components/conversations/conversations-header"
import { ConversationsLayout } from "../_components/conversations/conversations-layout"

type SearchParams = {
  search?: string
  dateFrom?: string
  dateTo?: string
  conversation?: string
}

export default async function InboxPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<SearchParams>
}) {
  const { id: businessId } = await params
  const session = await auth()

  if (!session) redirect("/login")
  if (!canAccessBusiness(session, businessId)) redirect("/businesses")

  const access = await getConversationsAccess(session)
  if (access.businessIds !== "all" && !access.businessIds.includes(businessId)) {
    redirect("/businesses")
  }

  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return (
      <div className="space-y-6">
        <ConversationsHeader role={session.user?.role} access={access} />
        <p className="text-center text-muted-foreground py-12">
          No business access configured. Contact your administrator.
        </p>
      </div>
    )
  }

  const paramsQ = await searchParams
  const searchQuery = paramsQ.search
  const dateFrom = paramsQ.dateFrom ? new Date(paramsQ.dateFrom) : undefined
  const dateTo = paramsQ.dateTo ? new Date(paramsQ.dateTo) : undefined
  const selectedConversation = paramsQ.conversation
  if (selectedConversation) {
    const threadBusinessId = selectedConversation.split(":")[1]
    if (threadBusinessId && threadBusinessId !== businessId) {
      redirect(`/businesses/${businessId}/inbox`)
    }
  }

  const [businessRow, conversations, stats, selectedThread] = await Promise.all([
    prisma.businesses.findUnique({
      where: { id: businessId },
      select: { id: true, name: true },
    }),
    getConversations({
      businessIds: access.businessIds,
      businessFilter: businessId,
      searchQuery,
      dateFrom,
      dateTo,
      limit: 100,
      offset: 0,
    }),
    access.canSeeAllStats
      ? getConversationStats({ businessIds: [businessId] })
      : null,
    selectedConversation
      ? getConversationThread({
          whatsappId: selectedConversation.split(":")[0],
          businessId: selectedConversation.split(":")[1],
        })
      : null,
  ])

  const businessesForUi = businessRow
    ? [{ id: businessRow.id, name: businessRow.name }]
    : []

  const whatsappNumbers = await prisma.whatsapp_numbers.findMany({
    where: { business_id: businessId, is_active: true },
    select: { id: true, phone_number: true, business_id: true },
    orderBy: { phone_number: "asc" },
  })

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Inbox</h1>
        <p className="text-sm text-muted-foreground">
          WhatsApp conversations for this business
        </p>
      </div>

      <ConversationsHeader role={session.user?.role} access={access} stats={stats} />

      <ConversationsLayout
        conversations={conversations}
        selectedThread={selectedThread}
        role={session.user?.role}
        businesses={businessesForUi}
        whatsappNumbers={whatsappNumbers}
        canFilterByBusiness={false}
        showBusinessColumn={false}
        scopedBusinessId={businessId}
        inboxBasePath={`/businesses/${businessId}/inbox`}
        initialFilters={{
          business: businessId,
          search: searchQuery,
          dateFrom: paramsQ.dateFrom,
          dateTo: paramsQ.dateTo,
        }}
      />
    </div>
  )
}
