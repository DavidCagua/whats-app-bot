"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useSearchParams } from "next/navigation"
import { ConversationGroup, ConversationThread } from "@/lib/conversations-queries"
import { ConversationsSidebar } from "./conversations-sidebar"
import { ConversationMessagesPanel } from "./conversation-messages-panel"
import { ConversationMessagesSkeleton } from "./conversation-messages-skeleton"
import { Card } from "@/components/ui/card"
import { MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"

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

  const [conversations, setConversations] = useState<ConversationGroup[]>(initialConversations)
  const [thread, setThread] = useState<ConversationThread | null>(initialThread)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(() => {
    if (initialThread) return `${initialThread.whatsapp_id}:${initialThread.business_id}`
    return searchParams.get("conversation")
  })
  const [threadLoading, setThreadLoading] = useState(false)
  // On mobile: "list" | "chat"
  const [mobileView, setMobileView] = useState<"list" | "chat">(
    initialThread ? "chat" : "list"
  )

  // AbortController + request token guard so a stale fetchThread can't clobber the active selection.
  const threadAbortRef = useRef<AbortController | null>(null)
  const activeRequestIdRef = useRef<string | null>(null)
  const selectedIdRef = useRef(selectedConversationId)
  selectedIdRef.current = selectedConversationId

  // Sync list from server when initial data changes (e.g. filter applied via router.push)
  useEffect(() => {
    setConversations(initialConversations)
  }, [initialConversations])

  const fetchThread = useCallback(async (conversationId: string) => {
    const [whatsappId, businessId] = conversationId.split(":")
    if (!whatsappId || !businessId) return

    threadAbortRef.current?.abort()
    const controller = new AbortController()
    threadAbortRef.current = controller
    activeRequestIdRef.current = conversationId

    try {
      const res = await fetch(
        `/api/conversations/thread?whatsappId=${encodeURIComponent(whatsappId)}&businessId=${encodeURIComponent(businessId)}`,
        { signal: controller.signal }
      )
      if (!res.ok) return
      const data = (await res.json()) as ConversationThread | null
      // Bail if the user moved on to a different conversation while this was in flight.
      if (selectedIdRef.current !== conversationId) return
      setThread(data)
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return
      // keep previous thread on transient errors
    } finally {
      if (activeRequestIdRef.current === conversationId) {
        setThreadLoading(false)
        activeRequestIdRef.current = null
      }
    }
  }, [])

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

  // Thread polling: refresh the currently-selected thread every 2.5s.
  useEffect(() => {
    if (!selectedConversationId) return
    const interval = setInterval(() => {
      // Don't poll while an explicit selection is still loading.
      if (activeRequestIdRef.current) return
      void fetchThread(selectedConversationId)
    }, THREAD_POLL_MS)
    return () => clearInterval(interval)
  }, [selectedConversationId, fetchThread])

  // List polling: every 7s
  useEffect(() => {
    const interval = setInterval(fetchList, LIST_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchList])

  // Cleanup any in-flight thread request on unmount.
  useEffect(() => {
    return () => threadAbortRef.current?.abort()
  }, [])

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      if (conversationId === selectedConversationId) {
        setMobileView("chat")
        return
      }
      setSelectedConversationId(conversationId)
      setMobileView("chat")
      // Show skeleton on the right pane immediately; clear the previous thread so we don't flash old content.
      setThread(null)
      setThreadLoading(true)
      void fetchThread(conversationId)
    },
    [selectedConversationId, fetchThread]
  )

  const handleBack = () => {
    setMobileView("list")
  }

  return (
    // Workspace: top bar h-14 + main padding p-6 (24px * 2)
    <div className="h-[calc(100vh-56px-48px)] flex gap-4 min-h-[420px]">

      {/* Sidebar — full width on mobile, fixed width on md+, hidden on mobile when in chat view */}
      <div
        className={cn(
          "flex flex-col min-w-0",
          "w-full md:w-[340px] lg:w-[380px] md:flex-shrink-0",
          mobileView === "chat" ? "hidden md:flex" : "flex"
        )}
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
          onSelectConversation={handleSelectConversation}
        />
      </div>

      {/* Messages panel — hidden on mobile when in list view */}
      <div
        className={cn(
          "flex-1 min-w-0",
          mobileView === "list" ? "hidden md:flex md:flex-col" : "flex flex-col"
        )}
      >
        {threadLoading && !thread ? (
          <ConversationMessagesSkeleton onBack={handleBack} />
        ) : thread ? (
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
