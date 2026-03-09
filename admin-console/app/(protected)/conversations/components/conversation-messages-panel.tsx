"use client"

import { ConversationThread } from "@/lib/conversations-queries"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { User, Building2, Phone, Bot } from "lucide-react"
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"

type ConversationMessagesPanelProps = {
  thread: ConversationThread
}

export function ConversationMessagesPanel({
  thread,
}: ConversationMessagesPanelProps) {
  const displayName = thread.customer_name || "Unknown Customer"

  return (
    <Card className="h-full flex flex-col">
      {/* Header */}
      <CardHeader className="border-b p-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
            <User className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold truncate">{displayName}</h3>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <div className="flex items-center gap-1">
                <Phone className="h-3 w-3" />
                <span>{thread.customer_phone}</span>
              </div>
              <div className="flex items-center gap-1">
                <Building2 className="h-3 w-3" />
                <span>{thread.business_name}</span>
              </div>
              <Badge variant="secondary" className="text-xs">
                {thread.total_messages} messages
              </Badge>
            </div>
          </div>
        </div>
      </CardHeader>

      {/* Messages */}
      <ScrollArea className="flex-1">
        <CardContent className="p-4">
          {thread.messages.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No messages in this conversation</p>
            </div>
          ) : (
            <div className="space-y-4">
              {thread.messages.map((message) => {
                const isUser = message.role === "user"
                const isAssistant = message.role === "assistant"

                return (
                  <div
                    key={message.id}
                    className={cn("flex gap-3", isAssistant && "flex-row-reverse")}
                  >
                    {/* Avatar */}
                    <div
                      className={cn(
                        "h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0",
                        isUser && "bg-blue-100 text-blue-600",
                        isAssistant && "bg-green-100 text-green-600"
                      )}
                    >
                      {isUser ? (
                        <User className="h-4 w-4" />
                      ) : (
                        <Bot className="h-4 w-4" />
                      )}
                    </div>

                    {/* Message Content */}
                    <div
                      className={cn(
                        "flex-1 space-y-1 max-w-[75%]",
                        isAssistant && "flex flex-col items-end"
                      )}
                    >
                      <div
                        className={cn(
                          "text-xs text-muted-foreground flex items-center gap-2",
                          isAssistant && "flex-row-reverse"
                        )}
                      >
                        <span className="font-medium">
                          {isUser ? "Customer" : "Assistant"}
                        </span>
                        <span>
                          {format(
                            new Date(message.timestamp),
                            "MMM d, yyyy 'at' h:mm a"
                          )}
                        </span>
                      </div>

                      <div
                        className={cn(
                          "rounded-lg p-3",
                          isUser && "bg-blue-50 text-blue-900",
                          isAssistant && "bg-green-50 text-green-900"
                        )}
                      >
                        <p className="text-sm whitespace-pre-wrap break-words">
                          {message.message}
                        </p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </ScrollArea>
    </Card>
  )
}
