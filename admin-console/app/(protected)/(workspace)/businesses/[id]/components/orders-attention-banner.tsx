"use client"

import Link from "next/link"
import { useState, useSyncExternalStore } from "react"
import { AlertTriangle, X } from "lucide-react"
import { useEventSource } from "@/lib/use-event-source"
import { cn } from "@/lib/utils"

type Counts = {
  pending: number
  inFlight: number
}

type OrdersAttentionBannerProps = {
  businessId: string
  initialCounts: Counts
}

const dismissKey = (businessId: string) => `orders:attention-banner-dismissed:${businessId}`

// Custom event we dispatch ourselves on dismiss — the standard
// `storage` event only fires across tabs, not within the same tab,
// so a user dismissing here wouldn't otherwise see the banner hide
// until a re-mount.
const DISMISS_EVENT = "orders-attention-banner-dismissed"

const parseDismissed = (raw: string | null): Counts | null => {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<Counts>
    return {
      pending: Number(parsed.pending) || 0,
      inFlight: Number(parsed.inFlight) || 0,
    }
  } catch {
    return null
  }
}

// Stable subscribe for useSyncExternalStore — must be reference-stable
// across renders or React will tear down + re-subscribe every render.
const subscribeDismissed = (onChange: () => void) => {
  if (typeof window === "undefined") return () => {}
  window.addEventListener("storage", onChange)
  window.addEventListener(DISMISS_EVENT, onChange)
  return () => {
    window.removeEventListener("storage", onChange)
    window.removeEventListener(DISMISS_EVENT, onChange)
  }
}

// Snapshot cache so getSnapshot returns a reference-stable value when
// the underlying localStorage string hasn't changed. Without this,
// parseDismissed allocates a fresh object on every read and
// useSyncExternalStore detects "change" on every render → infinite
// loop / re-render warning.
const dismissedSnapshotCache = new Map<string, { raw: string | null; parsed: Counts | null }>()

const getDismissedSnapshot = (businessId: string): Counts | null => {
  if (typeof window === "undefined") return null
  const raw = localStorage.getItem(dismissKey(businessId))
  const cached = dismissedSnapshotCache.get(businessId)
  if (cached && cached.raw === raw) return cached.parsed
  const parsed = parseDismissed(raw)
  dismissedSnapshotCache.set(businessId, { raw, parsed })
  return parsed
}

export function OrdersAttentionBanner({
  businessId,
  initialCounts,
}: OrdersAttentionBannerProps) {
  const [counts, setCounts] = useState<Counts>(initialCounts)

  // Sync with localStorage via the React-blessed external-store hook.
  // SSR returns null (no banner-dismissal info on the server); on the
  // client, hydration fires the subscribe callback and re-renders
  // with the parsed value. No setState-in-effect, no manual hydration
  // gymnastics.
  const dismissedAt = useSyncExternalStore<Counts | null>(
    subscribeDismissed,
    () => getDismissedSnapshot(businessId),
    () => null,
  )

  useEventSource<Counts>(
    `/api/orders/banner-counts/stream?businessId=${encodeURIComponent(businessId)}`,
    "counts",
    setCounts
  )

  const dismiss = () => {
    if (typeof window === "undefined") return
    localStorage.setItem(dismissKey(businessId), JSON.stringify(counts))
    // Invalidate the cache so the next snapshot read picks up the new
    // value, then dispatch our custom event so useSyncExternalStore
    // re-reads (the standard `storage` event only fires across tabs).
    dismissedSnapshotCache.delete(businessId)
    window.dispatchEvent(new Event(DISMISS_EVENT))
  }

  const nothingToShow = counts.pending === 0 && counts.inFlight === 0
  const dismissedCovers =
    dismissedAt !== null &&
    dismissedAt.pending >= counts.pending &&
    dismissedAt.inFlight >= counts.inFlight

  if (nothingToShow || dismissedCovers) return null

  const ordersHref = `/businesses/${businessId}/orders`

  return (
    <div
      className={cn(
        "flex items-center gap-3 border-b bg-amber-50 px-4 py-2 text-sm text-amber-900",
        "dark:bg-amber-950/40 dark:text-amber-100"
      )}
      role="status"
    >
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
        {counts.pending > 0 && (
          <Link href={ordersHref} className="hover:underline">
            <strong>{counts.pending}</strong>{" "}
            {counts.pending === 1 ? "pedido sin confirmar" : "pedidos sin confirmar"}
          </Link>
        )}
        {counts.pending > 0 && counts.inFlight > 0 && (
          <span aria-hidden className="text-amber-900/40 dark:text-amber-100/40">
            ·
          </span>
        )}
        {counts.inFlight > 0 && (
          <Link href={ordersHref} className="hover:underline">
            <strong>{counts.inFlight}</strong>{" "}
            {counts.inFlight === 1 ? "pedido sin entregar" : "pedidos sin entregar"}
          </Link>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Cerrar"
        className="rounded p-1 text-amber-900/70 hover:bg-amber-100 hover:text-amber-900 dark:text-amber-100/70 dark:hover:bg-amber-900/40 dark:hover:text-amber-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
