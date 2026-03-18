"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ConversationThread } from "@/lib/conversations-queries"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { User, Building2, Phone, Bot, Mic, Square } from "lucide-react"
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
  const [isRecording, setIsRecording] = useState(false)
  const [recordError, setRecordError] = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const draftRef = useRef(draft)
  draftRef.current = draft

  useEffect(() => {
    setLocalMessages(thread.messages)
    setAgentEnabled(thread.agent_enabled)
  }, [thread.whatsapp_id, thread.business_id, thread.total_messages, thread.agent_enabled])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [localMessages.length])

  const canSend = useMemo(
    () => Boolean(draft.trim()) && !isSending && !isRecording,
    [draft, isSending, isRecording]
  )

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

  const sendPayload = useCallback(
    (body: { text?: string; mediaUrl?: string; caption?: string }) => {
      return fetch("/api/conversations/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          whatsappId: thread.whatsapp_id,
          businessId: thread.business_id,
          ...body,
          ...(thread.phone_number_id ? { phoneNumberId: thread.phone_number_id } : {}),
          ...(thread.phone_number ? { phoneNumber: thread.phone_number } : {}),
        }),
      })
    },
    [thread.whatsapp_id, thread.business_id, thread.phone_number_id, thread.phone_number]
  )

  /** Upload a voice file and send it; handles optimistic update and rollback. */
  const sendVoiceNote = useCallback(
    async (file: File, caption: string) => {
      setSendError(null)
      setRecordError(null)
      setIsSending(true)
      const displayMessage = caption.trim() || "[audio]"
      const optimistic = {
        id: -Date.now(),
        whatsapp_id: thread.whatsapp_id,
        message: displayMessage,
        role: "assistant",
        timestamp: new Date().toISOString(),
        created_at: new Date().toISOString(),
      } as unknown as (typeof thread.messages)[number]
      setLocalMessages((prev) => [...prev, optimistic])

      try {
        const form = new FormData()
        form.set("file", file)
        form.set("business_id", thread.business_id)
        const uploadRes = await fetch("/api/conversations/upload-media", {
          method: "POST",
          body: form,
        })
        if (!uploadRes.ok) {
          const payload = await uploadRes.json().catch(() => ({}))
          throw new Error(payload?.error ?? "Upload failed")
        }
        const { url } = (await uploadRes.json()) as { url?: string }
        if (!url) throw new Error("No URL returned from upload")
        const res = await sendPayload({
          mediaUrl: url,
          ...(caption.trim() ? { caption: caption.trim() } : {}),
        })
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}))
          throw new Error(payload?.error || "Failed to send")
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Failed to send"
        setSendError(msg)
        setLocalMessages((prev) => prev.filter((m) => m !== optimistic))
      } finally {
        setIsSending(false)
      }
    },
    [thread.whatsapp_id, thread.business_id, sendPayload]
  )

  const onSend = async () => {
    const text = draft.trim()
    if (!text || isSending) return

    setIsSending(true)
    setSendError(null)

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
      const res = await sendPayload({ text })
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}))
        throw new Error(payload?.error || "Failed to send")
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to send"
      setSendError(msg)
      setLocalMessages((prev) => prev.filter((m) => m !== optimistic))
      setDraft(text)
    } finally {
      setIsSending(false)
    }
  }

  const startRecording = useCallback(async () => {
    setRecordError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm"
      const mr = new MediaRecorder(stream, { mimeType })
      mediaRecorderRef.current = mr
      mr.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data)
      }
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        streamRef.current = null
        mediaRecorderRef.current = null
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" })
        const file = new File([blob], `voice-note-${Date.now()}.webm`, { type: blob.type })
        const caption = draftRef.current
        setDraft("")
        setIsRecording(false)
        void sendVoiceNote(file, caption)
      }
      mr.start()
      setIsRecording(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not access microphone"
      setRecordError(msg)
    }
  }, [sendVoiceNote])

  const stopRecording = useCallback(() => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state === "recording") mr.stop()
  }, [])

  const onRecordClick = useCallback(() => {
    if (isRecording) stopRecording()
    else startRecording()
  }, [isRecording, startRecording, stopRecording])

  // Cleanup mic stream on unmount
  useEffect(() => {
    return () => {
      const stream = streamRef.current
      if (stream) stream.getTracks().forEach((t) => t.stop())
    }
  }, [])

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
                          "rounded-lg p-3 space-y-2",
                          isUser && "bg-blue-50 text-blue-900",
                          isAssistant && "bg-green-50 text-green-900"
                        )}
                      >
                        {message.message ? (
                          <p className="text-sm whitespace-pre-wrap break-words">
                            {message.message}
                          </p>
                        ) : null}
                        {message.attachments?.map((att) => (
                          <div key={att.id} className="space-y-1">
                            {att.type === "audio" ? (
                              att.url ? (
                                <audio
                                  src={att.url}
                                  controls
                                  className="max-w-full h-9"
                                  preload="metadata"
                                />
                              ) : (
                                <span className="text-xs text-muted-foreground italic">
                                  Audio — processing…
                                </span>
                              )
                            ) : att.url ? (
                              att.type === "image" ? (
                                <img
                                  src={att.url}
                                  alt="Attachment"
                                  className="max-w-full rounded max-h-48 object-contain"
                                />
                              ) : (
                                <a
                                  href={att.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-sm text-primary underline"
                                >
                                  View {att.type}
                                </a>
                              )
                            ) : null}
                            {att.transcript ? (
                              <p className="text-xs text-muted-foreground border-l-2 pl-2 mt-1">
                                <span className="font-medium">Transcript: </span>
                                {att.transcript}
                              </p>
                            ) : null}
                          </div>
                        ))}
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
        {recordError ? (
          <div className="text-xs text-destructive">{recordError}</div>
        ) : null}
        {isRecording ? (
          <div className="text-xs text-muted-foreground flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse" />
            Recording… Click the button again to stop and send.
          </div>
        ) : null}
        <div className="flex gap-2">
          <Button
            type="button"
            variant={isRecording ? "destructive" : "outline"}
            size="icon"
            className="flex-shrink-0"
            onClick={onRecordClick}
            disabled={isSending}
            title={isRecording ? "Stop and send" : "Record voice note"}
          >
            {isRecording ? (
              <Square className="h-4 w-4 fill-current" />
            ) : (
              <Mic className="h-4 w-4" />
            )}
          </Button>
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type a message or record a voice note..."
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                void onSend()
              }
            }}
            disabled={isSending || isRecording}
          />
          <Button onClick={() => void onSend()} disabled={!canSend}>
            {isSending ? "Sending..." : "Send"}
          </Button>
        </div>
      </div>
    </Card>
  )
}
