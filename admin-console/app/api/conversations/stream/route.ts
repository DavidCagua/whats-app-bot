import { NextRequest } from "next/server"
import { auth } from "@/lib/auth"
import {
  canAccessConversations,
  getConversationsAccess,
} from "@/lib/conversations-permissions"
import {
  getConversations,
  getConversationThread,
} from "@/lib/conversations-queries"
import { inboxBus, type InboxEvent } from "@/lib/inbox-bus"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"
export const maxDuration = 300

const HEARTBEAT_MS = 25_000
const COALESCE_MS = 150

type Mode = "thread" | "list"

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return new Response("Unauthorized", { status: 401 })
  }

  const params = request.nextUrl.searchParams
  const businessId = params.get("businessId")
  const whatsappId = params.get("whatsappId")
  const searchQuery = params.get("search") || undefined
  const dateFromParam = params.get("dateFrom")
  const dateToParam = params.get("dateTo")
  const dateFrom = dateFromParam ? new Date(dateFromParam) : undefined
  const dateTo = dateToParam ? new Date(dateToParam) : undefined

  if (!businessId) {
    return new Response("businessId is required", { status: 400 })
  }
  if (!canAccessConversations(session, businessId)) {
    return new Response("Access denied", { status: 403 })
  }

  const mode: Mode = whatsappId ? "thread" : "list"
  const access = await getConversationsAccess(session)
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return new Response("No business access", { status: 403 })
  }

  const encoder = new TextEncoder()

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false
      let coalesceTimer: NodeJS.Timeout | null = null
      let heartbeatTimer: NodeJS.Timeout | null = null

      const close = () => {
        if (closed) return
        closed = true
        if (coalesceTimer) clearTimeout(coalesceTimer)
        if (heartbeatTimer) clearInterval(heartbeatTimer)
        unsubscribe?.()
        try {
          controller.close()
        } catch {
          // ignore
        }
      }

      const write = (event: string, data: unknown) => {
        if (closed) return
        try {
          controller.enqueue(
            encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
          )
        } catch {
          close()
        }
      }

      const writeRaw = (chunk: string) => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(chunk))
        } catch {
          close()
        }
      }

      const sendSnapshot = async () => {
        try {
          if (mode === "thread") {
            const thread = await getConversationThread({
              whatsappId: whatsappId!,
              businessId,
            })
            write("snapshot", thread)
          } else {
            const conversations = await getConversations({
              businessIds: access.businessIds,
              businessFilter: businessId,
              searchQuery,
              dateFrom,
              dateTo,
              limit: 50,
              offset: 0,
            })
            write("snapshot", conversations)
          }
        } catch (err) {
          console.error("[stream] snapshot failed", err)
          write("error", { message: "snapshot_failed" })
        }
      }

      const scheduleSnapshot = () => {
        if (coalesceTimer || closed) return
        coalesceTimer = setTimeout(() => {
          coalesceTimer = null
          void sendSnapshot()
        }, COALESCE_MS)
      }

      const handleEvent = (event: InboxEvent) => {
        // For thread mode, ignore agent events that don't match our scope
        // (the bus already filters on businessId/whatsappId).
        if (mode === "thread") {
          // Any event in scope means refetch the thread.
          scheduleSnapshot()
          return
        }
        // List mode: only message/attachment events affect ordering or
        // last-message preview. Agent toggles don't show in the list.
        if (event.type === "message" || event.type === "attachment") {
          scheduleSnapshot()
        }
      }

      const unsubscribe = inboxBus.subscribe(
        {
          businessId,
          whatsappId: whatsappId ?? undefined,
          eventTypes: ["message", "attachment", "agent"],
        },
        handleEvent
      )

      // Initial snapshot.
      void sendSnapshot()

      // Heartbeat to keep intermediaries from closing the connection.
      heartbeatTimer = setInterval(() => {
        writeRaw(": ping\n\n")
      }, HEARTBEAT_MS)

      // Client disconnect.
      request.signal.addEventListener("abort", close)
    },
  })

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  })
}
