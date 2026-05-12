// Mirror of app/services/order_status_machine.py for the admin console.
// Keep in sync with the Python source — both must agree on legal transitions.

export const ORDER_STATUSES = [
  "pending",
  "confirmed",
  "out_for_delivery",
  "ready_for_pickup",
  "completed",
  "cancelled",
] as const

export type OrderStatus = (typeof ORDER_STATUSES)[number]

const ALLOWED_NEXT: Record<OrderStatus, ReadonlySet<OrderStatus>> = {
  pending: new Set<OrderStatus>(["confirmed", "cancelled"]),
  confirmed: new Set<OrderStatus>([
    "out_for_delivery",
    "ready_for_pickup",
    "completed",
    "cancelled",
  ]),
  out_for_delivery: new Set<OrderStatus>(["completed", "cancelled"]),
  ready_for_pickup: new Set<OrderStatus>(["completed", "cancelled"]),
  completed: new Set<OrderStatus>(),
  cancelled: new Set<OrderStatus>(),
}

export function isValidStatus(s: string): s is OrderStatus {
  return (ORDER_STATUSES as readonly string[]).includes(s)
}

export function allowedNext(from: OrderStatus | string | null | undefined): ReadonlySet<OrderStatus> {
  if (!from || !isValidStatus(from)) return new Set()
  return ALLOWED_NEXT[from]
}

export function canTransition(
  from: OrderStatus | string | null | undefined,
  to: OrderStatus
): boolean {
  return allowedNext(from).has(to)
}

/**
 * Admin-only transition set: every status except the current one.
 *
 * The bot still goes through `allowedNext()` (mirror of the Python state
 * machine) so it can never accidentally undo a cancellation or revive a
 * completed order. The admin console UI uses this looser version so a
 * human can recover from "I clicked the wrong status" without touching
 * the bot's safety net.
 */
export function adminAllowedNext(
  from: OrderStatus | string | null | undefined
): ReadonlySet<OrderStatus> {
  if (!from || !isValidStatus(from)) return new Set(ORDER_STATUSES)
  return new Set(ORDER_STATUSES.filter((s) => s !== from))
}

export function adminCanTransition(
  from: OrderStatus | string | null | undefined,
  to: OrderStatus
): boolean {
  return adminAllowedNext(from).has(to)
}

/**
 * Filter a status set by fulfillment type. Pickup orders never go
 * `out_for_delivery` (the courier path), and delivery orders never go
 * `ready_for_pickup` (the counter path). Use this on top of
 * `adminAllowedNext` / `allowedNext` when rendering the operator
 * dropdown so wrong-fulfillment options never appear.
 */
export function filterStatusesByFulfillment<T extends Iterable<OrderStatus>>(
  statuses: T,
  fulfillmentType: string | null | undefined,
): OrderStatus[] {
  const isPickup = (fulfillmentType ?? "").toLowerCase() === "pickup"
  const excluded: OrderStatus = isPickup ? "out_for_delivery" : "ready_for_pickup"
  return Array.from(statuses).filter((s) => s !== excluded)
}

// Returns the column on `orders` that should be set to NOW() when
// entering this status. null = no dedicated timestamp.
export function timestampFieldFor(
  status: OrderStatus
): "confirmed_at" | "ready_at" | "completed_at" | "cancelled_at" | null {
  switch (status) {
    case "confirmed":
      return "confirmed_at"
    case "ready_for_pickup":
      return "ready_at"
    case "completed":
      return "completed_at"
    case "cancelled":
      return "cancelled_at"
    default:
      return null
  }
}

export const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "Pendiente",
  confirmed: "Confirmado",
  out_for_delivery: "En camino",
  ready_for_pickup: "Listo para recoger",
  completed: "Completado",
  cancelled: "Cancelado",
}
