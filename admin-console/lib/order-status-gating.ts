// Time-gates for status transitions in the admin UI.
//
// Operators control status from a dropdown — the existing
// adminAllowedNext is permissive (any status except the current one)
// so they can recover from mis-clicks. The downside: nothing stops a
// fresh order from being marked "completed" 30 seconds after placement,
// which hides real fulfillment time from analytics and trains operators
// into a one-click-done flow that masks slow kitchens / late drivers.
//
// These thresholds enforce a minimum wall-clock window from order
// placement (`created_at`) before specific statuses become available.
// Other statuses stay free.
//
// Client-only enforcement for now — the matching server check lives in
// updateOrderStatus once we decide whether a determined operator should
// be able to bypass via a direct API call.

import type { OrderStatus } from "./order-status"

export const STATUS_MIN_MINUTES: Partial<Record<OrderStatus, number>> = {
  out_for_delivery: 25,
  ready_for_pickup: 15,
  completed: 40,
}

export type StatusGate = {
  allowed: boolean
  /** Minutes still needed before this status unlocks. 0 when allowed. */
  minutesRemaining: number
  /** Configured minimum, or null when the status is ungated. */
  thresholdMinutes: number | null
}

export function canSetStatus(
  target: OrderStatus,
  createdAtIso: string | null | undefined,
  now: number,
): StatusGate {
  const threshold = STATUS_MIN_MINUTES[target]
  if (threshold == null) {
    return { allowed: true, minutesRemaining: 0, thresholdMinutes: null }
  }
  if (!createdAtIso) {
    // No placement timestamp → fail open. Better to let staff act than
    // strand orders behind a missing-data condition.
    return { allowed: true, minutesRemaining: 0, thresholdMinutes: threshold }
  }
  const placedAt = new Date(createdAtIso).getTime()
  if (!Number.isFinite(placedAt)) {
    return { allowed: true, minutesRemaining: 0, thresholdMinutes: threshold }
  }
  const elapsedMin = Math.floor((now - placedAt) / 60_000)
  if (elapsedMin >= threshold) {
    return { allowed: true, minutesRemaining: 0, thresholdMinutes: threshold }
  }
  return {
    allowed: false,
    minutesRemaining: Math.max(1, threshold - elapsedMin),
    thresholdMinutes: threshold,
  }
}
