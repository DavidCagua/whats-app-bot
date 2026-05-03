"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ConversationThread } from "@/lib/conversations-queries"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { User, Building2, Phone, Bot, Mic, Square, ArrowLeft } from "lucide-react"
import { format } from "date-fns"
import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

type ConversationMessagesPanelProps = {
  thread: ConversationThread
  onBack?: () => void
}

type ThreadMessage = ConversationThread["messages"][number]

const OPTIMISTIC_MATCH_WINDOW_MS = 60_000
const VOICE_PLACEHOLDER = "[audio]"
const OLDER_PAGE_LIMIT = 50
const SCROLL_TOP_TRIGGER_PX = 150
const SCROLL_BOTTOM_PINNED_PX = 80
const COMPOSER_LOCK_TOOLTIP =
  "El bot está atendiendo. Apaga el switch para responder tú."

/**
 * Merge a server snapshot with the local list. Preserves:
 *   - older paginated messages already loaded (id < min server id, id > 0)
 *   - optimistic outbound messages not yet confirmed by the server (id < 0)
 *
 * The server snapshot only ever covers the latest window, so any local
 * message older than the server window is a paginated prefix that must
 * survive the merge.
 */
function mergeMessages(
  local: ThreadMessage[],
  server: ThreadMessage[]
): ThreadMessage[] {
  if (server.length === 0) return local

  const minServerId = server.reduce(
    (min, m) => (m.id < min ? m.id : min),
    server[0].id
  )

  const olderPrefix = local.filter((m) => m.id > 0 && m.id < minServerId)
  const optimistic = local.filter((m) => m.id < 0)

  const stillPending = optimistic.filter((opt) => {
    const optTs = new Date(opt.timestamp).getTime()
    return !server.some((srv) => {
      if (srv.role !== opt.role) return false
      const srvTs = new Date(srv.timestamp).getTime()
      if (Math.abs(srvTs - optTs) > OPTIMISTIC_MATCH_WINDOW_MS) return false
      if (opt.message !== VOICE_PLACEHOLDER && srv.message === opt.message) return true
      if (
        opt.message === VOICE_PLACEHOLDER &&
        srv.role === "assistant" &&
        Array.isArray(srv.attachments) &&
        srv.attachments.length > 0
      ) {
        return true
      }
      return false
    })
  })

  if (olderPrefix.length === 0 && stillPending.length === 0) return server
  return [...olderPrefix, ...server, ...stillPending].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  )
}

export function ConversationMessagesPanel({
  thread,
  onBack,
}: ConversationMessagesPanelProps) {
  const displayName = thread.customer_name || "Unknown Customer"
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const isAtBottomRef = useRef(true)
  const [draft, setDraft] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [agentEnabled, setAgentEnabled] = useState(thread.agent_enabled)
  const [isTogglingAgent, setIsTogglingAgent] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [olderError, setOlderError] = useState<string | null>(null)

  const [localMessages, setLocalMessages] = useState(thread.messages)
  const [isRecording, setIsRecording] = useState(false)
  const [recordError, setRecordError] = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const draftRef = useRef(draft)
  useEffect(() => {
    draftRef.current = draft
  }, [draft])

  const prevThreadIdRef = useRef<string | null>(null)
  const isTogglingAgentRef = useRef(isTogglingAgent)
  useEffect(() => {
    isTogglingAgentRef.current = isTogglingAgent
  }, [isTogglingAgent])

  // Scroll behaviour bookkeeping.
  const forceScrollToBottomRef = useRef(false)
  const scrollAdjustmentRef = useRef<{ prevHeight: number; prevTop: number } | null>(null)
  const loadingOlderRef = useRef(false)
  useEffect(() => {
    loadingOlderRef.current = loadingOlder
  }, [loadingOlder])

  const totalMessages = thread.total_messages
  const hasMoreOlder = useMemo(
    () => localMessages.filter((m) => m.id > 0).length < totalMessages,
    [localMessages, totalMessages]
  )
  const hasMoreOlderRef = useRef(hasMoreOlder)
  useEffect(() => {
    hasMoreOlderRef.current = hasMoreOlder
  }, [hasMoreOlder])

  const composerLocked = agentEnabled
  const composerDisabled = composerLocked || isSending || isRecording
  const sendDisabled = composerLocked || !draft.trim() || isSending || isRecording

  const getViewport = useCallback((): HTMLDivElement | null => {
    const root = scrollAreaRef.current
    if (!root) return null
    return root.querySelector<HTMLDivElement>("[data-radix-scroll-area-viewport]")
  }, [])

  const fetchOlder = useCallback(async () => {
    if (loadingOlderRef.current || !hasMoreOlderRef.current) return
    const oldestId = localMessages.reduce<number | null>((min, m) => {
      if (m.id <= 0) return min
      if (min == null || m.id < min) return m.id
      return min
    }, null)
    if (oldestId == null) return

    const viewport = getViewport()
    if (viewport) {
      scrollAdjustmentRef.current = {
        prevHeight: viewport.scrollHeight,
        prevTop: viewport.scrollTop,
      }
    }

    setLoadingOlder(true)
    setOlderError(null)
    try {
      const url = `/api/conversations/thread?whatsappId=${encodeURIComponent(
        thread.whatsapp_id
      )}&businessId=${encodeURIComponent(
        thread.business_id
      )}&before=${oldestId}&limit=${OLDER_PAGE_LIMIT}`
      const res = await fetch(url)
      if (!res.ok) throw new Error("Failed to load older messages")
      const data = (await res.json()) as ConversationThread | null
      const olderPage = data?.messages ?? []
      if (olderPage.length === 0) {
        scrollAdjustmentRef.current = null
        return
      }
      setLocalMessages((prev) => {
        const knownIds = new Set(prev.map((m) => m.id))
        const fresh = olderPage.filter((m) => !knownIds.has(m.id))
        if (fresh.length === 0) return prev
        return [...fresh, ...prev]
      })
    } catch (e) {
      scrollAdjustmentRef.current = null
      setOlderError(e instanceof Error ? e.message : "Failed to load older messages")
    } finally {
      setLoadingOlder(false)
    }
  }, [thread.whatsapp_id, thread.business_id, localMessages, getViewport])

  // Reset on thread switch; merge in place on same-thread snapshot updates.
  useEffect(() => {
    const threadId = `${thread.whatsapp_id}:${thread.business_id}`
    if (prevThreadIdRef.current !== threadId) {
      setLocalMessages(thread.messages)
      setAgentEnabled(thread.agent_enabled)
      prevThreadIdRef.current = threadId
      forceScrollToBottomRef.current = true
      isAtBottomRef.current = true
      return
    }
    setLocalMessages((prev) => mergeMessages(prev, thread.messages))
    if (!isTogglingAgentRef.current) {
      setAgentEnabled(thread.agent_enabled)
    }
  }, [thread.whatsapp_id, thread.business_id, thread.messages, thread.agent_enabled])

  // Track at-bottom state and trigger lazy load when scrolling near top.
  useEffect(() => {
    const viewport = getViewport()
    if (!viewport) return

    const update = () => {
      const distanceFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      isAtBottomRef.current = distanceFromBottom < SCROLL_BOTTOM_PINNED_PX

      if (
        viewport.scrollTop < SCROLL_TOP_TRIGGER_PX &&
        hasMoreOlderRef.current &&
        !loadingOlderRef.current
      ) {
        void fetchOlder()
      }
    }
    update()
    viewport.addEventListener("scroll", update, { passive: true })
    return () => viewport.removeEventListener("scroll", update)
  }, [getViewport, fetchOlder])

  // Apply scroll-position adjustments after layout has settled. Priority:
  //   1. Initial paint of a new thread → jump to bottom.
  //   2. Just prepended an older page → keep visible content stable.
  //   3. Otherwise → smooth scroll to bottom only when user was at bottom.
  //
  // Depends on the localMessages reference (not its length) so that switching
  // between two threads with the same message count still runs the force-bottom
  // branch — length-only deps were the bug that made selection feel "stuck at
  // the oldest message" on every click.
  useEffect(() => {
    const viewport = getViewport()
    if (!viewport) return

    if (forceScrollToBottomRef.current) {
      forceScrollToBottomRef.current = false
      isAtBottomRef.current = true
      // Two rAFs: first lets the new tree commit, second runs after Radix
      // ScrollArea has measured its viewport. Setting scrollTop directly
      // is reliable on the radix viewport; scrollIntoView often isn't.
      requestAnimationFrame(() => {
        viewport.scrollTop = viewport.scrollHeight
        requestAnimationFrame(() => {
          viewport.scrollTop = viewport.scrollHeight
        })
      })
      return
    }
    if (scrollAdjustmentRef.current) {
      const { prevHeight, prevTop } = scrollAdjustmentRef.current
      scrollAdjustmentRef.current = null
      requestAnimationFrame(() => {
        const delta = viewport.scrollHeight - prevHeight
        viewport.scrollTop = prevTop + delta
      })
      return
    }
    if (!isAtBottomRef.current) return
    requestAnimationFrame(() => {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
    })
  }, [localMessages, getViewport])

  const onToggleAgent = async (next: boolean) => {
    const previous = agentEnabled
    setAgentEnabled(next)
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
      if (!res.ok) throw new Error("Failed to update")
    } catch {
      setAgentEnabled(previous)
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

  const sendVoiceNote = useCallback(
    async (file: File, caption: string) => {
      setSendError(null)
      setRecordError(null)
      setIsSending(true)
      const displayMessage = caption.trim() || VOICE_PLACEHOLDER
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
    [thread, sendPayload]
  )

  const onSend = async () => {
    const text = draft.trim()
    if (!text || isSending || composerLocked) return

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
    if (composerLocked) return
    if (isRecording) stopRecording()
    else startRecording()
  }, [isRecording, startRecording, stopRecording, composerLocked])

  useEffect(() => {
    return () => {
      const stream = streamRef.current
      if (stream) stream.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return (
    <Card className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <CardHeader className="border-b p-3 sm:p-4 flex-shrink-0">
        <div className="flex items-center gap-2 sm:gap-3">
          {/* Back button — mobile only */}
          {onBack && (
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden flex-shrink-0 h-8 w-8"
              onClick={onBack}
              aria-label="Back to conversations"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}

          <div className="h-9 w-9 sm:h-10 sm:w-10 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
            <User className="h-4 w-4 sm:h-5 sm:w-5 text-primary" />
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="font-semibold truncate text-sm sm:text-base">{displayName}</h3>

            {/* Info row — wraps on small screens */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-0.5">
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Phone className="h-3 w-3 flex-shrink-0" />
                <span className="truncate max-w-[120px]">{thread.customer_phone}</span>
              </div>
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <Building2 className="h-3 w-3 flex-shrink-0" />
                <span className="truncate max-w-[100px]">{thread.business_name}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <Badge
                  variant={agentEnabled ? "default" : "secondary"}
                  className="text-xs px-2 py-0 gap-1"
                >
                  {agentEnabled ? (
                    <Bot className="h-3 w-3" />
                  ) : (
                    <User className="h-3 w-3" />
                  )}
                  {agentEnabled ? "Bot atiende" : "Tú respondes"}
                </Badge>
                <Switch
                  checked={agentEnabled}
                  disabled={isTogglingAgent}
                  onCheckedChange={(checked) => void onToggleAgent(checked)}
                  className="scale-75 origin-left"
                  aria-label={
                    agentEnabled
                      ? "Apagar el bot para responder tú"
                      : "Activar el bot para que atienda"
                  }
                />
              </div>
              <Badge variant="outline" className="text-xs px-1.5 py-0">
                {thread.total_messages} msgs
              </Badge>
            </div>
          </div>
        </div>
      </CardHeader>

      {/* Messages */}
      <ScrollArea ref={scrollAreaRef} className="flex-1">
        <CardContent className="p-3 sm:p-4">
          {loadingOlder && (
            <div className="text-center text-xs text-muted-foreground py-2">
              Cargando mensajes anteriores…
            </div>
          )}
          {olderError && (
            <div className="text-center text-xs text-destructive py-2">
              {olderError}
            </div>
          )}
          {localMessages.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No messages in this conversation</p>
            </div>
          ) : (
            <div className="space-y-3 sm:space-y-4">
              {localMessages.map((message) => {
                const isUser = message.role === "user"
                const isAssistant = message.role === "assistant"

                return (
                  <div
                    key={message.id}
                    className={cn("flex gap-2 sm:gap-3", isAssistant && "flex-row-reverse")}
                  >
                    {/* Avatar */}
                    <div
                      className={cn(
                        "h-7 w-7 sm:h-8 sm:w-8 rounded-full flex items-center justify-center flex-shrink-0 mt-1",
                        isUser && "bg-blue-100 text-blue-600",
                        isAssistant && "bg-green-100 text-green-600"
                      )}
                    >
                      {isUser ? (
                        <User className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                      ) : (
                        <Bot className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                      )}
                    </div>

                    {/* Bubble */}
                    <div
                      className={cn(
                        "flex-1 space-y-1 max-w-[80%] sm:max-w-[75%]",
                        isAssistant && "flex flex-col items-end"
                      )}
                    >
                      <div
                        className={cn(
                          "text-xs text-muted-foreground flex items-center gap-1.5",
                          isAssistant && "flex-row-reverse"
                        )}
                      >
                        <span className="font-medium">
                          {isUser ? "Customer" : "Assistant"}
                        </span>
                        <span className="hidden sm:inline">
                          {format(new Date(message.timestamp), "MMM d, yyyy 'at' h:mm a")}
                        </span>
                        <span className="sm:hidden">
                          {format(new Date(message.timestamp), "h:mm a")}
                        </span>
                      </div>

                      <div
                        className={cn(
                          "rounded-2xl px-3 py-2 sm:rounded-lg sm:p-3 space-y-2",
                          isUser && "bg-blue-50 text-blue-900",
                          isAssistant && "bg-green-50 text-green-900"
                        )}
                      >
                        {message.message ? (
                          <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
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
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                  src={att.url}
                                  alt="Attachment"
                                  loading="lazy"
                                  decoding="async"
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
      <TooltipProvider delayDuration={150}>
        <div className="border-t p-3 flex-shrink-0 space-y-2">
          {sendError && <p className="text-xs text-destructive">{sendError}</p>}
          {recordError && <p className="text-xs text-destructive">{recordError}</p>}
          {isRecording && (
            <div className="text-xs text-muted-foreground flex items-center gap-2">
              <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              Recording… tap again to stop and send.
            </div>
          )}

          <div className="flex gap-2 items-end">
            {/* Voice note button */}
            <Tooltip>
              <TooltipTrigger asChild>
                {/* Wrapper span keeps Radix tooltip pointer-events alive even when the button is disabled. */}
                <span className="flex-shrink-0">
                  <Button
                    type="button"
                    variant={isRecording ? "destructive" : "outline"}
                    size="icon"
                    className="h-9 w-9"
                    onClick={onRecordClick}
                    disabled={composerLocked || isSending}
                    aria-label={
                      isRecording ? "Detener y enviar" : "Grabar nota de voz"
                    }
                  >
                    {isRecording ? (
                      <Square className="h-4 w-4 fill-current" />
                    ) : (
                      <Mic className="h-4 w-4" />
                    )}
                  </Button>
                </span>
              </TooltipTrigger>
              {composerLocked && (
                <TooltipContent>{COMPOSER_LOCK_TOOLTIP}</TooltipContent>
              )}
            </Tooltip>

            {/* Textarea — wraps in a tooltip trigger only when locked, so normal typing is unaffected. */}
            {composerLocked ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex-1">
                    <Textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      placeholder="Bot activo — apaga el switch para escribir"
                      rows={1}
                      className="resize-none min-h-[36px] max-h-[120px] py-2 w-full text-sm cursor-not-allowed"
                      style={{ fieldSizing: "content" } as React.CSSProperties}
                      disabled
                      aria-label="Composer locked while bot is handling the conversation"
                    />
                  </span>
                </TooltipTrigger>
                <TooltipContent>{COMPOSER_LOCK_TOOLTIP}</TooltipContent>
              </Tooltip>
            ) : (
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Type a message..."
                rows={1}
                className="resize-none min-h-[36px] max-h-[120px] py-2 flex-1 text-sm"
                style={{ fieldSizing: "content" } as React.CSSProperties}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault()
                    void onSend()
                  }
                }}
                disabled={composerDisabled}
              />
            )}

            {/* Send button */}
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="flex-shrink-0">
                  <Button
                    onClick={() => void onSend()}
                    disabled={sendDisabled}
                    className="h-9 px-3 sm:px-4"
                  >
                    <span className="hidden sm:inline">{isSending ? "Sending..." : "Send"}</span>
                    <span className="sm:hidden">{isSending ? "…" : "↑"}</span>
                  </Button>
                </span>
              </TooltipTrigger>
              {composerLocked && (
                <TooltipContent>{COMPOSER_LOCK_TOOLTIP}</TooltipContent>
              )}
            </Tooltip>
          </div>
        </div>
      </TooltipProvider>
    </Card>
  )
}
