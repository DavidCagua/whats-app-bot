"use client"

import { ConversationGroup } from "@/lib/conversations-queries"
import { Badge } from "@/components/ui/badge"
import { Building2 } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { cn } from "@/lib/utils"

type ConversationListItemProps = {
  conversation: ConversationGroup
  isSelected: boolean
  showBusiness: boolean
  onClick: () => void
}

export function ConversationListItem({
  conversation,
  isSelected,
  showBusiness,
  onClick,
}: ConversationListItemProps) {
  const displayName = conversation.customer_name || conversation.whatsapp_id
  const timeAgo = formatDistanceToNow(new Date(conversation.last_timestamp), {
    addSuffix: false,
  })

  return (
    <div
      onClick={onClick}
      className={cn(
        "p-3 cursor-pointer hover:bg-accent transition-colors",
        isSelected && "bg-accent"
      )}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <h4 className="font-semibold text-sm truncate flex-1">{displayName}</h4>
        <span className="text-xs text-muted-foreground whitespace-nowrap">{timeAgo}</span>
      </div>

      {showBusiness && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground mb-1">
          <Building2 className="h-3 w-3" />
          <span className="truncate">{conversation.business_name}</span>
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
