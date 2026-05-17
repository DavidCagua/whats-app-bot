"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ConversationThread } from "@/lib/conversations-queries"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  User,
  Building2,
  Phone,
  Bot,
  Mic,
  Square,
  ArrowLeft,
  ChevronDown,
  AlertTriangle,
} from "lucide-react"
import { format, isToday, isYesterday, differenceInCalendarDays } from "date-fns"
import { es } from "date-fns/locale"
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

type RenderItem =
  | { kind: "separator"; key: string; label: string }
  | { kind: "message"; key: string; message: ThreadMessage }

const OPTIMISTIC_MATCH_WINDOW_MS = 60_000
const VOICE_PLACEHOLDER = "[audio]"
const OLDER_PAGE_LIMIT = 50
const SCROLL_TOP_TRIGGER_PX = 150
const SCROLL_BOTTOM_PINNED_PX = 80
const COMPOSER_LOCK_TOOLTIP =
  "El bot está atendiendo. Apaga el switch para responder tú."

/**
 * Friendly Spanish day label for a message timestamp:
 *   - Today  → "Hoy"
 *   - Yesterday → "Ayer"
 *   - Within last 6 days → weekday name capitalised ("Lunes")
 *   - Otherwise → "5 de mayo de 2026"
 */
function formatDayLabel(timestamp: string | Date): string {
  const date = new Date(timestamp)
  if (isToday(date)) return "Hoy"
  if (isYesterday(date)) return "Ayer"
  const days = differenceInCalendarDays(new Date(), date)
  if (days >= 0 && days < 7) {
    const weekday = format(date, "EEEE", { locale: es })
    return weekday.charAt(0).toUpperCase() + weekday.slice(1)
  }
  return format(date, "d 'de' MMMM 'de' yyyy", { locale: es })
}

function dayKey(timestamp: string | Date): string {
  return format(new Date(timestamp), "yyyy-MM-dd")
}

/**
 * Build a flat list of render items (day separators + messages). A
 * separator is inserted before the first message of every new calendar
 * day, mirroring WhatsApp's behaviour. Optimistic messages with id < 0
 * still get a separator if their timestamp crosses a day boundary.
 */
function buildRenderItems(messages: ThreadMessage[]): RenderItem[] {
  const items: RenderItem[] = []
  let prevDay = ""
  for (const m of messages) {
    const k = dayKey(m.timestamp)
    if (k !== prevDay) {
      items.push({ kind: "separator", key: `sep-${k}`, label: formatDayLabel(m.timestamp) })
      prevDay = k
    }
    items.push({ kind: "message", key: `msg-${m.id}`, message: m })
  }
  return items
}

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
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [agentEnabled, setAgentEnabled] = useState(thread.agent_enabled)
  const [handoffReason, setHandoffReason] = useState<string | null>(thread.handoff_reason)
  const [isTogglingAgent, setIsTogglingAgent] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [olderError, setOlderError] = useState<string | null>(null)
  // Counter of new server messages received while the user is scrolled up.
  // Drives the "↓ N nuevos mensajes" pill that lets them jump to the bottom.
  const [unreadWhileScrolledUp, setUnreadWhileScrolledUp] = useState(0)
  // True whenever the user has scrolled away from the bottom. Drives the
  // round chevron-down button that lets them jump back to the latest message
  // (mirrors WhatsApp's behaviour).
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)

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

  // Highest server-confirmed message id seen so far on this thread. Used to
  // detect "newly arrived" messages on snapshot updates so we can bump the
  // unread counter when the user is scrolled up.
  const lastSeenMaxIdRef = useRef<number>(0)

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

  const renderItems = useMemo<RenderItem[]>(
    () => buildRenderItems(localMessages),
    [localMessages]
  )

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
      setHandoffReason(thread.handoff_reason)
      prevThreadIdRef.current = threadId
      forceScrollToBottomRef.current = true
      isAtBottomRef.current = true
      setShowJumpToLatest(false)
      // Reset unread bookkeeping for the new thread.
      setUnreadWhileScrolledUp(0)
      lastSeenMaxIdRef.current = thread.messages.reduce(
        (max, m) => (m.id > max ? m.id : max),
        0
      )
      return
    }
    // Same-thread snapshot: detect newly arrived server messages (id > prev
    // max). If the user is scrolled up, bump the unread counter so the pill
    // can offer a one-click jump to the bottom.
    const prevMaxId = lastSeenMaxIdRef.current
    let arrivedDelta = 0
    let newMaxId = prevMaxId
    for (const m of thread.messages) {
      if (m.id <= 0) continue
      if (m.id > newMaxId) newMaxId = m.id
      if (m.id > prevMaxId) arrivedDelta += 1
    }
    lastSeenMaxIdRef.current = newMaxId
    if (arrivedDelta > 0 && !isAtBottomRef.current) {
      setUnreadWhileScrolledUp((c) => c + arrivedDelta)
    }
    setLocalMessages((prev) => mergeMessages(prev, thread.messages))
    if (!isTogglingAgentRef.current) {
      setAgentEnabled(thread.agent_enabled)
      setHandoffReason(thread.handoff_reason)
    }
  }, [
    thread.whatsapp_id,
    thread.business_id,
    thread.messages,
    thread.agent_enabled,
    thread.handoff_reason,
  ])

  // Track at-bottom state and trigger lazy load when scrolling near top.
  // Only triggers fetchOlder on actual user-driven scroll events — never on
  // the initial mount, where viewport.scrollTop is naturally 0 before the
  // force-bottom rAF runs. Without this gate, the synchronous initial
  // measurement would race fetchOlder against force-bottom and the older-
  // page prepend's scroll-adjustment would land the user mid-thread instead
  // of at the latest message.
  useEffect(() => {
    const viewport = getViewport()
    if (!viewport) return

    const onScroll = () => {
      const distanceFromBottom =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
      const atBottom = distanceFromBottom < SCROLL_BOTTOM_PINNED_PX
      isAtBottomRef.current = atBottom
      setShowJumpToLatest(!atBottom)

      // Scrolled back to the bottom — implicitly acknowledge any unread
      // messages that arrived while the user was up in history.
      if (atBottom) {
        setUnreadWhileScrolledUp((c) => (c === 0 ? c : 0))
      }

      if (
        viewport.scrollTop < SCROLL_TOP_TRIGGER_PX &&
        hasMoreOlderRef.current &&
        !loadingOlderRef.current
      ) {
        void fetchOlder()
      }
    }
    viewport.addEventListener("scroll", onScroll, { passive: true })
    return () => viewport.removeEventListener("scroll", onScroll)
  }, [getViewport, fetchOlder])

  const jumpToLatest = useCallback(() => {
    const viewport = getViewport()
    if (!viewport) return
    requestAnimationFrame(() => {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
    })
    isAtBottomRef.current = true
    setShowJumpToLatest(false)
    setUnreadWhileScrolledUp(0)
  }, [getViewport])

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
      // Drop any queued scroll-adjustment from a stale older-page fetch — on
      // a fresh thread mount we want bottom, not "preserve previous scroll".
      scrollAdjustmentRef.current = null
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
    const previousEnabled = agentEnabled
    const previousReason = handoffReason
    setAgentEnabled(next)
    // Optimistic clear: re-enabling drops the handoff badge instantly so
    // staff sees the colored treatment go away as soon as they flip the
    // switch. Server clears the column too — see app/api/conversations/agent-enabled.
    if (next) setHandoffReason(null)
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
      setAgentEnabled(previousEnabled)
      setHandoffReason(previousReason)
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
      // The textarea is `disabled` while sending, which clears focus. Restore
      // it after the send settles so the operator can keep typing.
      if (!composerLocked) {
        requestAnimationFrame(() => composerRef.current?.focus())
      }
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

      {/* Auto-handoff warning: appears when the bot disabled itself
          (e.g. customer asked for status >50min after placing an order,
          or sent a payment-proof image/PDF). Sits between the header
          and the messages so staff can't miss it. */}
      {handoffReason === "delivery_handoff" && !agentEnabled && (
        <div
          role="alert"
          className="flex items-start gap-2 border-y border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100 sm:px-4 sm:text-sm"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex-1 leading-snug">
            <strong>Seguimiento de domicilio pendiente.</strong> El cliente preguntó por el
            estado del pedido y el bot pasó la conversación a un humano. Verifica
            con el domiciliario y responde tú; cuando termines, vuelve a activar
            el bot.
          </div>
        </div>
      )}

      {handoffReason === "payment_proof" && !agentEnabled && (
        <div
          role="alert"
          className="flex items-start gap-2 border-y border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100 sm:px-4 sm:text-sm"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex-1 leading-snug">
            <strong>Comprobante de pago recibido.</strong> El cliente envió un recibo
            (imagen o PDF) y el bot pasó la conversación a un humano. Verifica el
            envío contra el pedido y confirma con el cliente; cuando termines,
            vuelve a activar el bot.
          </div>
        </div>
      )}

      {handoffReason === "human_request" && !agentEnabled && (
        <div
          role="alert"
          className="flex items-start gap-2 border-y border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100 sm:px-4 sm:text-sm"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex-1 leading-snug">
            <strong>El cliente pidió hablar con un asesor.</strong> El bot pasó
            la conversación a un humano. Atiende personalmente y cuando
            termines, vuelve a activar el bot.
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="relative flex-1 min-h-0">
        <ScrollArea ref={scrollAreaRef} className="h-full">
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
            {renderItems.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <p>No messages in this conversation</p>
              </div>
            ) : (
              <div className="space-y-3 sm:space-y-4">
                {renderItems.map((item) => {
                  if (item.kind === "separator") {
                    return (
                      <div
                        key={item.key}
                        className="flex items-center justify-center py-1"
                      >
                        <div className="px-3 py-1 rounded-full bg-muted text-muted-foreground text-[11px] font-medium uppercase tracking-wide shadow-sm">
                          {item.label}
                        </div>
                      </div>
                    )
                  }
                  const message = item.message
                  const isUser = message.role === "user"
                  const isAssistant = message.role === "assistant"

                  return (
                    <div
                      key={item.key}
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
                            {format(new Date(message.timestamp), "h:mm a")}
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

        {/* Floating "↓ N nuevos mensajes" pill — visible while user is
            scrolled up and at least one new server message has arrived. */}
        {unreadWhileScrolledUp > 0 && (
          <Button
            type="button"
            size="sm"
            onClick={jumpToLatest}
            className="absolute bottom-3 left-1/2 -translate-x-1/2 shadow-lg gap-1.5 rounded-full h-8 px-3 z-10"
            aria-label="Saltar a los mensajes nuevos"
          >
            <ChevronDown className="h-4 w-4" />
            {unreadWhileScrolledUp === 1
              ? "1 mensaje nuevo"
              : `${unreadWhileScrolledUp} mensajes nuevos`}
          </Button>
        )}

        {/* Plain round "scroll to latest" button — shown whenever the user
            is scrolled up but no new messages have arrived (otherwise the
            pill above takes over). */}
        {showJumpToLatest && unreadWhileScrolledUp === 0 && (
          <Button
            type="button"
            variant="secondary"
            size="icon"
            onClick={jumpToLatest}
            className="absolute bottom-3 right-3 h-9 w-9 rounded-full shadow-lg z-10"
            aria-label="Saltar al último mensaje"
          >
            <ChevronDown className="h-4 w-4" />
          </Button>
        )}
      </div>

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
                ref={composerRef}
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
                disabled={composerLocked || isRecording}
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
