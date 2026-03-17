"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { ConversationThread } from "@/lib/conversations-queries"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { User, Building2, Phone, Bot } from "lucide-react"
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"

type ConversationMessagesPanelProps = {
  thread: ConversationThread
}

export function ConversationMessagesPanel({
  thread,
}: ConversationMessagesPanelProps) {
  const displayName = thread.customer_name || "Unknown Customer"
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [draft, setDraft] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [agentEnabled, setAgentEnabled] = useState(thread.agent_enabled)
  const [isTogglingAgent, setIsTogglingAgent] = useState(false)

  const [localMessages, setLocalMessages] = useState(thread.messages)

  useEffect(() => {
    setLocalMessages(thread.messages)
    setAgentEnabled(thread.agent_enabled)
  }, [thread.whatsapp_id, thread.business_id, thread.total_messages, thread.agent_enabled])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [localMessages.length])

  const canSend = useMemo(() => Boolean(draft.trim()) && !isSending, [draft, isSending])

  const onToggleAgent = async (next: boolean) => {
    setIsTogglingAgent(true)
    try {
      const res = await fetch("/api/conversations/agent-enabled", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whatsappId: thread.whatsapp_id,
          businessId: thread.business_id,
          agentEnabled: next,
        }),
      })
      if (!res.ok) {
        throw new Error("Failed to update")
      }
      setAgentEnabled(next)
    } catch {
      // keep previous state
    } finally {
      setIsTogglingAgent(false)
    }
  }

  const onSend = async () => {
    const text = draft.trim()
    if (!text || isSending) return

    setIsSending(true)
    setSendError(null)

    // optimistic append
    const optimistic = {
      id: -Date.now(),
      whatsapp_id: thread.whatsapp_id,
      message: text,
      role: "assistant",
      timestamp: new Date().toISOString(),
      created_at: new Date().toISOString(),
    } as unknown as (typeof thread.messages)[number]

    setLocalMessages((prev) => [...prev, optimistic])
    setDraft("")

    try {
      const res = await fetch("/api/conversations/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whatsappId: thread.whatsapp_id,
          businessId: thread.business_id,
          text,
          ...(thread.phone_number_id ? { phoneNumberId: thread.phone_number_id } : {}),
          ...(thread.phone_number ? { phoneNumber: thread.phone_number } : {}),
        }),
      })

      if (!res.ok) {
        const payload = await res.json().catch(() => ({}))
        throw new Error(payload?.error || "Failed to send")
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to send"
      setSendError(msg)
      // rollback optimistic
      setLocalMessages((prev) => prev.filter((m) => m !== optimistic))
      setDraft(text)
    } finally {
      setIsSending(false)
    }
  }

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
              <div className="flex items-center gap-2">
                <Bot className="h-3 w-3" />
                <span>Agent</span>
                <Switch
                  checked={agentEnabled}
                  disabled={isTogglingAgent}
                  onCheckedChange={(checked) => void onToggleAgent(checked)}
                />
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
          {localMessages.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No messages in this conversation</p>
            </div>
          ) : (
            <div className="space-y-4">
              {localMessages.map((message) => {
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
          <div ref={messagesEndRef} />
        </CardContent>
      </ScrollArea>

      {/* Composer */}
      <div className="border-t p-3 flex-shrink-0 space-y-2">
        {sendError ? (
          <div className="text-xs text-destructive">{sendError}</div>
        ) : null}
        <div className="flex gap-2">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type a message..."
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void onSend()
              }
            }}
            disabled={isSending}
          />
          <Button onClick={onSend} disabled={!canSend}>
            {isSending ? "Sending..." : "Send"}
          </Button>
        </div>
      </div>
    </Card>
  )
}
