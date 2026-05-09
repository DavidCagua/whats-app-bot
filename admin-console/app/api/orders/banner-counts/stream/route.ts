import { NextRequest } from "next/server"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { getOrderBannerCounts } from "@/lib/orders-queries"
import { inboxBus } from "@/lib/inbox-bus"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"
export const maxDuration = 300

const HEARTBEAT_MS = 25_000
const COALESCE_MS = 200

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return new Response("Unauthorized", { status: 401 })
  }

  const businessId = request.nextUrl.searchParams.get("businessId")
  if (!businessId) {
    return new Response("businessId is required", { status: 400 })
  }
  if (!canAccessBusiness(session, businessId)) {
    return new Response("Access denied", { status: 403 })
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

      const sendCounts = async () => {
        try {
          const counts = await getOrderBannerCounts(businessId)
          write("counts", counts)
        } catch (err) {
          console.error("[orders-banner-counts-stream] count failed", err)
        }
      }

      const scheduleCounts = () => {
        if (coalesceTimer || closed) return
        coalesceTimer = setTimeout(() => {
          coalesceTimer = null
          void sendCounts()
        }, COALESCE_MS)
      }

      const unsubscribe = inboxBus.subscribe(
        { businessId, eventTypes: ["order"] },
        () => scheduleCounts()
      )

      void sendCounts()

      heartbeatTimer = setInterval(() => {
        writeRaw(": ping\n\n")
      }, HEARTBEAT_MS)

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
