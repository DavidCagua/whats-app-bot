"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
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

export function OrdersAttentionBanner({
  businessId,
  initialCounts,
}: OrdersAttentionBannerProps) {
  const [counts, setCounts] = useState<Counts>(initialCounts)
  const [dismissedAt, setDismissedAt] = useState<Counts | null>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    setDismissedAt(parseDismissed(localStorage.getItem(dismissKey(businessId))))
  }, [businessId])

  useEventSource<Counts>(
    `/api/orders/banner-counts/stream?businessId=${encodeURIComponent(businessId)}`,
    "counts",
    setCounts
  )

  const dismiss = () => {
    if (typeof window === "undefined") return
    localStorage.setItem(dismissKey(businessId), JSON.stringify(counts))
    setDismissedAt(counts)
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
