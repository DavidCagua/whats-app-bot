"use client"

import { useState, useEffect, useCallback } from "react"
import { useSearchParams } from "next/navigation"
import { ConversationGroup, ConversationThread } from "@/lib/conversations-queries"
import { ConversationsSidebar } from "./conversations-sidebar"
import { ConversationMessagesPanel } from "./conversation-messages-panel"
import { Card } from "@/components/ui/card"
import { MessageSquare } from "lucide-react"

const THREAD_POLL_MS = 2500
const LIST_POLL_MS = 7000

type ConversationsLayoutProps = {
  conversations: ConversationGroup[]
  selectedThread: ConversationThread | null
  role?: string
  businesses: Array<{ id: string; name: string }>
  whatsappNumbers: Array<{ id: string; phone_number: string; business_id: string }>
  canFilterByBusiness: boolean
  showBusinessColumn: boolean
  /** When set, list polling always scopes API calls to this business. */
  scopedBusinessId?: string
  inboxBasePath: string
  initialFilters: {
    business?: string
    search?: string
    dateFrom?: string
    dateTo?: string
  }
}

export function ConversationsLayout({
  conversations: initialConversations,
  selectedThread: initialThread,
  role,
  businesses,
  whatsappNumbers,
  canFilterByBusiness,
  showBusinessColumn,
  scopedBusinessId,
  inboxBasePath,
  initialFilters,
}: ConversationsLayoutProps) {
  const searchParams = useSearchParams()
  const conversationParam = searchParams.get("conversation")

  const [conversations, setConversations] = useState<ConversationGroup[]>(initialConversations)
  const [thread, setThread] = useState<ConversationThread | null>(initialThread)
  // On mobile: "list" | "chat"
  const [mobileView, setMobileView] = useState<"list" | "chat">("list")

  // Sync list from server when initial data changes (e.g. filter applied)
  useEffect(() => {
    setConversations(initialConversations)
  }, [initialConversations])

  // Sync thread + switch to chat view when a conversation is selected
  useEffect(() => {
    if (!conversationParam) {
      setThread(null)
      setMobileView("list")
      return
    }
    if (initialThread && `${initialThread.whatsapp_id}:${initialThread.business_id}` === conversationParam) {
      setThread(initialThread)
      setMobileView("chat")
    } else {
      setThread(null)
    }
  }, [conversationParam, initialThread])

  const fetchThread = useCallback(async () => {
    if (!conversationParam) return
    const [whatsappId, businessId] = conversationParam.split(":")
    if (!whatsappId || !businessId) return
    try {
      const res = await fetch(
        `/api/conversations/thread?whatsappId=${encodeURIComponent(whatsappId)}&businessId=${encodeURIComponent(businessId)}`
      )
      if (res.ok) {
        const data = await res.json()
        setThread(data)
      }
    } catch {
      // keep previous thread on error
    }
  }, [conversationParam])

  const fetchList = useCallback(async () => {
    const params = new URLSearchParams()
    const business = scopedBusinessId ?? searchParams.get("business")
    const search = searchParams.get("search")
    const dateFrom = searchParams.get("dateFrom")
    const dateTo = searchParams.get("dateTo")
    if (business) params.set("business", business)
    if (search) params.set("search", search)
    if (dateFrom) params.set("dateFrom", dateFrom)
    if (dateTo) params.set("dateTo", dateTo)
    params.set("limit", "50")
    params.set("offset", "0")
    try {
      const res = await fetch(`/api/conversations?${params.toString()}`)
      if (res.ok) {
        const data = await res.json()
        setConversations(data)
      }
    } catch {
      // keep previous list on error
    }
  }, [searchParams, scopedBusinessId])

  // Thread polling: every 2–3s when a conversation is selected
  useEffect(() => {
    if (!conversationParam) return
    const interval = setInterval(fetchThread, THREAD_POLL_MS)
    return () => clearInterval(interval)
  }, [conversationParam, fetchThread])

  // List polling: every 5–10s
  useEffect(() => {
    const interval = setInterval(fetchList, LIST_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchList])

  const selectedConversationId = thread
    ? `${thread.whatsapp_id}:${thread.business_id}`
    : null

  const handleConversationSelect = () => {
    setMobileView("chat")
  }

  const handleBack = () => {
    setMobileView("list")
  }

  return (
    // Workspace: top bar h-14 + main padding p-6 (24px * 2)
    <div className="h-[calc(100vh-56px-48px)] flex gap-4 min-h-[420px]">

      {/* Sidebar — full width on mobile, fixed width on md+, hidden on mobile when in chat view */}
      <div
        className={[
          "flex flex-col min-w-0",
          // Mobile: full width, hidden when chatting
          "w-full md:w-[340px] lg:w-[380px] md:flex-shrink-0",
          mobileView === "chat" ? "hidden md:flex" : "flex",
        ].join(" ")}
      >
        <ConversationsSidebar
          conversations={conversations}
          selectedConversationId={selectedConversationId}
          role={role}
          businesses={businesses}
          whatsappNumbers={whatsappNumbers}
          canFilterByBusiness={canFilterByBusiness}
          showBusinessColumn={showBusinessColumn}
          inboxBasePath={inboxBasePath}
          initialFilters={initialFilters}
          onConversationSelect={handleConversationSelect}
        />
      </div>

      {/* Messages panel — hidden on mobile when in list view */}
      <div
        className={[
          "flex-1 min-w-0",
          mobileView === "list" ? "hidden md:flex md:flex-col" : "flex flex-col",
        ].join(" ")}
      >
        {thread ? (
          <ConversationMessagesPanel thread={thread} onBack={handleBack} />
        ) : (
          <Card className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground px-6">
              <MessageSquare className="h-14 w-14 mx-auto mb-4 opacity-20" />
              <p className="text-base font-medium">Select a conversation</p>
              <p className="text-sm mt-1">Choose a conversation from the list to view messages</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
