"use client"

import { ConversationGroup, ConversationThread } from "@/lib/conversations-queries"
import { ConversationsSidebar } from "./conversations-sidebar"
import { ConversationMessagesPanel } from "./conversation-messages-panel"
import { Card } from "@/components/ui/card"
import { MessageSquare } from "lucide-react"

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
  conversations,
  selectedThread,
  role,
  businesses,
  whatsappNumbers,
  canFilterByBusiness,
  showBusinessColumn,
  initialFilters,
}: ConversationsLayoutProps) {
  return (
    <div className="flex gap-4 h-[calc(100vh-280px)]">
      {/* Left Sidebar - Conversations List */}
      <div className="w-[380px] flex-shrink-0">
        <ConversationsSidebar
          conversations={conversations}
          selectedConversationId={
            selectedThread
              ? `${selectedThread.whatsapp_id}:${selectedThread.business_id}`
              : null
          }
          role={role}
          businesses={businesses}
          whatsappNumbers={whatsappNumbers}
          canFilterByBusiness={canFilterByBusiness}
          showBusinessColumn={showBusinessColumn}
          initialFilters={initialFilters}
        />
      </div>

      {/* Right Panel - Messages */}
      <div className="flex-1 min-w-0">
        {selectedThread ? (
          <ConversationMessagesPanel thread={selectedThread} />
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
