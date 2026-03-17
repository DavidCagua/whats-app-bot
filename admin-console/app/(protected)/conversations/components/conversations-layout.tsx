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
  initialFilters,
}: ConversationsLayoutProps) {
  const searchParams = useSearchParams()
  const conversationParam = searchParams.get("conversation")

  const [conversations, setConversations] = useState<ConversationGroup[]>(initialConversations)
  const [thread, setThread] = useState<ConversationThread | null>(initialThread)

  // Sync list from server when initial data changes (e.g. filter applied)
  useEffect(() => {
    setConversations(initialConversations)
  }, [initialConversations])

  // Sync thread from server when selected conversation changes (e.g. user picked another chat)
  useEffect(() => {
    if (!conversationParam) {
      setThread(null)
      return
    }
    if (initialThread && `${initialThread.whatsapp_id}:${initialThread.business_id}` === conversationParam) {
      setThread(initialThread)
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
    const business = searchParams.get("business")
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
  }, [searchParams])

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

  return (
    <div className="flex gap-4 h-[calc(100vh-280px)]">
      <div className="w-[380px] flex-shrink-0">
        <ConversationsSidebar
          conversations={conversations}
          selectedConversationId={selectedConversationId}
          role={role}
          businesses={businesses}
          whatsappNumbers={whatsappNumbers}
          canFilterByBusiness={canFilterByBusiness}
          showBusinessColumn={showBusinessColumn}
          initialFilters={initialFilters}
        />
      </div>

      <div className="flex-1 min-w-0">
        {thread ? (
          <ConversationMessagesPanel thread={thread} />
        ) : (
          <Card className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <MessageSquare className="h-16 w-16 mx-auto mb-4 opacity-20" />
              <p className="text-lg font-medium">Select a conversation</p>
              <p className="text-sm">Choose a conversation from the list to view messages</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
