import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { getConversationsAccess } from "@/lib/conversations-permissions"
import { getConversations, getConversationStats, getConversationThread } from "@/lib/conversations-queries"
import { isSuperAdmin } from "@/lib/permissions"
import { ConversationsHeader } from "./components/conversations-header"
import { ConversationsLayout } from "./components/conversations-layout"

type SearchParams = {
  business?: string
  search?: string
  dateFrom?: string
  dateTo?: string
  conversation?: string
}

export default async function ConversationsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>
}) {
  const session = await auth()

  if (!session) {
    redirect("/login")
  }

  // Get user's access permissions
  const access = await getConversationsAccess(session)

  // If user has no business access, show empty state
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return (
      <div className="space-y-6">
        <ConversationsHeader role={session.user?.role} access={access} />
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <p className="text-muted-foreground">
            No business access configured. Contact your administrator.
          </p>
        </div>
      </div>
    )
  }

  // Await and parse search parameters
  const params = await searchParams
  const businessFilter = params.business
  const searchQuery = params.search
  const dateFrom = params.dateFrom ? new Date(params.dateFrom) : undefined
  const dateTo = params.dateTo ? new Date(params.dateTo) : undefined
  const selectedConversation = params.conversation

  // Fetch conversations and stats in parallel
  const [conversations, stats, selectedThread] = await Promise.all([
    getConversations({
      businessIds: access.businessIds,
      businessFilter,
      searchQuery,
      dateFrom,
      dateTo,
      limit: 100,
      offset: 0,
    }),
    access.canSeeAllStats
      ? getConversationStats({ businessIds: access.businessIds })
      : null,
    // If a conversation is selected, fetch its thread
    selectedConversation
      ? getConversationThread({
          whatsappId: selectedConversation.split(":")[0],
          businessId: selectedConversation.split(":")[1],
        })
      : null,
  ])

  return (
    <div className="space-y-6">
      <ConversationsHeader role={session.user?.role} access={access} stats={stats} />

      <ConversationsLayout
        conversations={conversations}
        selectedThread={selectedThread}
        role={session.user?.role}
        businesses={access.businesses}
        whatsappNumbers={access.whatsappNumbers}
        canFilterByBusiness={access.canFilterByBusiness}
        showBusinessColumn={isSuperAdmin(session) || access.businesses.length > 1}
        initialFilters={{
          business: businessFilter,
          search: searchQuery,
          dateFrom: params.dateFrom,
          dateTo: params.dateTo,
        }}
      />
    </div>
  )
}
