"use client"

import { useEffect, useRef } from "react"

const RECONNECT_BASE_MS = 1_000
const RECONNECT_CAP_MS = 30_000

/**
 * Subscribes to a server-sent events endpoint with exponential-backoff
 * reconnect (1s → 30s) and visibility-aware pause/resume. Pass null to
 * disable the connection without unmounting the consumer.
 *
 * The handler is read through a ref so callers don't need to wrap it in
 * useCallback to keep the EventSource stable.
 */
export function useEventSource<T>(
  url: string | null,
  eventName: string,
  onMessage: (payload: T) => void
) {
  const handlerRef = useRef(onMessage)
  useEffect(() => {
    handlerRef.current = onMessage
  })

  useEffect(() => {
    if (!url || typeof window === "undefined") return
    let stopped = false
    let attempt = 0
    let es: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    const clearReconnectTimer = () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    }

    const open = () => {
      if (stopped) return
      es = new EventSource(url)
      es.addEventListener(eventName, (e) => {
        attempt = 0
        try {
          handlerRef.current(JSON.parse((e as MessageEvent).data) as T)
        } catch (err) {
          console.error("[sse] invalid payload", err)
        }
      })
      es.onerror = () => {
        es?.close()
        es = null
        if (stopped) return
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** attempt,
          RECONNECT_CAP_MS
        )
        attempt += 1
        clearReconnectTimer()
        reconnectTimer = setTimeout(open, delay)
      }
    }

    const onVisibilityChange = () => {
      if (document.hidden) {
        es?.close()
        es = null
        clearReconnectTimer()
      } else if (!es && !reconnectTimer) {
        attempt = 0
        open()
      }
    }

    open()
    document.addEventListener("visibilitychange", onVisibilityChange)

    return () => {
      stopped = true
      document.removeEventListener("visibilitychange", onVisibilityChange)
      clearReconnectTimer()
      es?.close()
    }
  }, [url, eventName])
}
