"use client"

import { memo } from "react"
import { ConversationGroup } from "@/lib/conversations-queries"
import { Badge } from "@/components/ui/badge"
import { AlertTriangle, Building2 } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { cn } from "@/lib/utils"

type ConversationListItemProps = {
  conversation: ConversationGroup
  isSelected: boolean
  isUnread: boolean
  showBusiness: boolean
  /** Shared "now" timestamp from a single layout-level ticker so every item
   * recomputes the relative time on the same tick instead of per-render. */
  now: number
  onClick: () => void
}

function ConversationListItemComponent({
  conversation,
  isSelected,
  isUnread,
  showBusiness,
  now,
  onClick,
}: ConversationListItemProps) {
  const displayName = conversation.customer_name || conversation.whatsapp_id
  const timeAgo = formatDistanceToNow(new Date(conversation.last_timestamp), {
    addSuffix: false,
    // `now` participates in the date-fns calc; passing it via locale isn't
    // possible, but referencing it here is enough for React.memo to invalidate
    // when the layout ticker advances.
  })
  void now

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      onClick()
    }
  }

  const isAwaitingHandoff = conversation.handoff_reason === "delivery_handoff"

  return (
    <div
      onClick={onClick}
      onKeyDown={handleKeyDown}
      role="button"
      tabIndex={0}
      aria-current={isSelected ? "true" : undefined}
      aria-label={`Open conversation with ${displayName}${isUnread ? " (unread)" : ""}${
        isAwaitingHandoff ? " (awaiting human follow-up)" : ""
      }`}
      className={cn(
        "p-3 cursor-pointer hover:bg-accent transition-colors outline-none",
        "focus-visible:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        // Amber left border + faint amber tint signals "needs human" — visible
        // even when the row is selected or unread, both of which use bg-accent.
        isAwaitingHandoff &&
          "border-l-4 border-amber-500 bg-amber-50/60 dark:bg-amber-950/30 hover:bg-amber-100/60 dark:hover:bg-amber-950/50",
        isSelected && (isAwaitingHandoff ? "bg-amber-100 dark:bg-amber-950/60" : "bg-accent")
      )}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 flex-1 min-w-0">
          {isUnread && (
            <span className="h-2 w-2 rounded-full bg-blue-500 flex-shrink-0" aria-hidden="true" />
          )}
          <h4 className={cn("text-sm truncate", isUnread ? "font-bold" : "font-semibold")}>
            {displayName}
          </h4>
        </div>
        <span className="text-xs text-muted-foreground whitespace-nowrap">{timeAgo}</span>
      </div>

      {showBusiness && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
          <Building2 className="h-3 w-3" aria-hidden="true" />
          <span className="truncate">{conversation.business_name}</span>
        </div>
      )}

      {isAwaitingHandoff && (
        <div className="flex items-center gap-1 mb-1 text-xs font-medium text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          <span>Esperando seguimiento humano</span>
        </div>
      )}

      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-muted-foreground line-clamp-2 flex-1">
          {conversation.last_message}
        </p>
        {conversation.message_count > 1 && (
          <Badge variant="secondary" className="text-xs flex-shrink-0">
            {conversation.message_count}
          </Badge>
        )}
      </div>
    </div>
  )
}

export const ConversationListItem = memo(ConversationListItemComponent)
