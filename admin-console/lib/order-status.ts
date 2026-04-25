// Mirror of app/services/order_status_machine.py for the admin console.
// Keep in sync with the Python source — both must agree on legal transitions.

export const ORDER_STATUSES = [
  "pending",
  "confirmed",
  "out_for_delivery",
  "completed",
  "cancelled",
] as const

export type OrderStatus = (typeof ORDER_STATUSES)[number]

const ALLOWED_NEXT: Record<OrderStatus, ReadonlySet<OrderStatus>> = {
  pending: new Set<OrderStatus>(["confirmed", "cancelled"]),
  confirmed: new Set<OrderStatus>(["out_for_delivery", "completed", "cancelled"]),
  out_for_delivery: new Set<OrderStatus>(["completed", "cancelled"]),
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

// Returns the column on `orders` that should be set to NOW() when
// entering this status. null = no dedicated timestamp.
export function timestampFieldFor(status: OrderStatus): "confirmed_at" | "completed_at" | "cancelled_at" | null {
  switch (status) {
    case "confirmed":
      return "confirmed_at"
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
  completed: "Completado",
  cancelled: "Cancelado",
}
