"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { Bell, BellOff, BellRing, ChevronLeft, ChevronRight, Pencil, Printer } from "lucide-react"
import { format } from "date-fns"
import { toast } from "sonner"
import { useRouter } from "next/navigation"
import { updateOrderStatus } from "@/lib/actions/orders"
import { canSetStatus } from "@/lib/order-status-gating"
import {
  type OrderStatus,
  STATUS_LABELS,
  adminAllowedNext,
  isValidStatus,
} from "@/lib/order-status"
import { useEventSource } from "@/lib/use-event-source"
import type { OrderRow } from "@/lib/orders-queries"
import type {
  CustomerOption,
  ProductOption,
} from "@/lib/orders-create-data"
import { EditOrderDialog } from "./edit-order-dialog"
import {
  CancelOrderDialog,
  type CancelDialogResult,
} from "./cancel-order-dialog"
import { formatStoredReason } from "@/lib/order-cancel-reasons"
import {
  getAlertsEnabled,
  playChime,
  setAlertsEnabled as persistAlertsEnabled,
  unlockAndPlayTest,
} from "@/lib/order-alert"
import {
  type DateRange,
  type RangePreset,
  detectKind,
  formatRangeLabel,
  presetRange,
  shiftRangeByDays,
} from "@/lib/orders-date-range"
import { cn, formatDisplayNumber } from "@/lib/utils"

const PULSE_DURATION_MS = 5_000
const TOAST_DURATION_MS = 8_000
const ELAPSED_TICK_MS = 30_000

// In-flight statuses get "X min ago" framing — staff cares about how long
// the order has been hanging. Terminal statuses (completed, cancelled) get
// the concrete date because the elapsed time stops being actionable.
const IN_FLIGHT_STATUSES = new Set(["pending", "confirmed", "out_for_delivery"])

const formatElapsedSince = (iso: string, now: number): string => {
  const startedAt = new Date(iso).getTime()
  if (!Number.isFinite(startedAt)) return "—"
  const diffMin = Math.max(0, Math.round((now - startedAt) / 60_000))
  if (diffMin < 1) return "ahora"
  if (diffMin < 60) return `${diffMin} min`
  const hours = Math.floor(diffMin / 60)
  const mins = diffMin % 60
  if (hours < 24) return mins === 0 ? `${hours} h` : `${hours} h ${mins} min`
  const days = Math.floor(hours / 24)
  const remHours = hours % 24
  return remHours === 0 ? `${days} d` : `${days} d ${remHours} h`
}

const formatAmount = (value: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(value)

const statusVariant = (
  status: string
): "default" | "secondary" | "destructive" | "outline" => {
  switch (status) {
    case "completed":
      return "default"
    case "out_for_delivery":
    case "confirmed":
      return "secondary"
    case "pending":
      return "outline"
    case "cancelled":
      return "destructive"
    default:
      return "outline"
  }
}

const labelFor = (status: string): string =>
  isValidStatus(status) ? STATUS_LABELS[status] : status

const capitalize = (value: string | null | undefined): string => {
  if (!value) return "—"
  const trimmed = value.trim()
  if (!trimmed) return "—"
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
}

const formatAmountForToast = (value: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(value)

const orderToastSummary = (o: OrderRow) => {
  const items =
    o.items.length > 0
      ? o.items.map((i) => `${i.quantity}× ${i.productName}`).join(", ")
      : "Sin ítems"
  return `${items} · ${formatAmountForToast(o.total_amount)}`
}

export function OrdersTable({
  businessId,
  businessName,
  initialOrders,
  initialRange,
  products,
  customers,
}: {
  businessId: string
  businessName: string
  initialOrders: OrderRow[]
  initialRange: DateRange
  products: ProductOption[]
  customers: CustomerOption[]
}) {
  const router = useRouter()
  // Pull initialRange's primitives out so the effect's dep array
  // references stable scalars (satisfies react-hooks/exhaustive-deps
  // without depending on the parent's object identity, which would
  // trigger spurious re-syncs on every parent render).
  const { from: initialFrom, to: initialTo } = initialRange
  const [orders, setOrders] = useState<OrderRow[]>(initialOrders)
  const [updating, setUpdating] = useState<string | null>(null)
  const [range, setRange] = useState<DateRange>(initialRange)
  const [editingOrderId, setEditingOrderId] = useState<string | null>(null)
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null)
  // Single shared "now" used by every in-flight row to render its elapsed
  // time. Single setInterval beats one per row.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ELAPSED_TICK_MS)
    return () => clearInterval(id)
  }, [])
  // Server snapshots overwrite local state, but only after a brief settle —
  // when the user changes filter, the URL update + server-rendered initialOrders
  // hand the new list down via props. The SSE stream then reconnects with the
  // new from/to and replaces the snapshot. Keep both in sync.
  useEffect(() => {
    setRange({ from: initialFrom, to: initialTo })
  }, [initialFrom, initialTo])

  const kind = useMemo(() => detectKind(range), [range])

  const updateRange = useCallback(
    (next: DateRange) => {
      const params = new URLSearchParams()
      params.set("from", next.from)
      params.set("to", next.to)
      router.replace(`?${params.toString()}`, { scroll: false })
    },
    [router]
  )

  const onPreset = (preset: RangePreset) => updateRange(presetRange(preset))
  const onShiftDay = (days: number) => updateRange(shiftRangeByDays(range, days))
  const onPickDate = (value: string) => {
    if (!value) return
    updateRange({ from: value, to: value })
  }

  // Per-device opt-in for the audio chime (localStorage). When false, the
  // visual alerts (pulse, toast, title flash) still fire — only sound is gated.
  const [alertsEnabled, setAlertsEnabled] = useState(false)
  // Audio context unlock state for the current page session. Even when the
  // localStorage pref is true, the browser autoplay policy may suspend audio
  // until the user interacts with the page. Reactivar = re-run the unlock.
  const [audioUnlocked, setAudioUnlocked] = useState(false)
  // Pulse marker per recently arrived order id; auto-clears after 5s.
  const [recentIds, setRecentIds] = useState<Set<string>>(new Set())
  // Unread count drives the document title flash while the tab is hidden.
  const [unreadCount, setUnreadCount] = useState(0)

  // Track in-flight admin status updates so an SSE snapshot landing
  // mid-PATCH doesn't snap the badge back to the server's stale value.
  const pendingStatusRef = useRef<Map<string, OrderStatus>>(new Map())
  // Initialised on the first SSE snapshot so we don't fire alerts for the
  // initial backlog of orders that were already in the DB on page load.
  const seenIdsRef = useRef<Set<string> | null>(null)
  // Hold latest alertsEnabled in a ref so the snapshot handler reads the
  // current value without needing to re-bind on every toggle.
  const alertsEnabledRef = useRef(false)
  useEffect(() => {
    alertsEnabledRef.current = alertsEnabled
  }, [alertsEnabled])

  // Read persisted opt-in once on mount.
  useEffect(() => {
    setAlertsEnabled(getAlertsEnabled())
  }, [])

  // Document title flash while the tab is hidden. Restores on focus.
  useEffect(() => {
    const baseTitle = `Pedidos · ${businessName}`
    if (unreadCount === 0) {
      document.title = baseTitle
      return
    }
    const noun = unreadCount === 1 ? "pedido" : "pedidos"
    document.title = `🔔 (${unreadCount}) Nuevo ${noun} — ${baseTitle}`
    return () => {
      document.title = baseTitle
    }
  }, [unreadCount, businessName])

  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) setUnreadCount(0)
    }
    document.addEventListener("visibilitychange", onVisibility)
    return () => document.removeEventListener("visibilitychange", onVisibility)
  }, [])

  const handleNewOrders = useCallback((newOnes: OrderRow[]) => {
    setRecentIds((prev) => {
      const next = new Set(prev)
      newOnes.forEach((o) => next.add(o.id))
      return next
    })
    newOnes.forEach((o) => {
      window.setTimeout(() => {
        setRecentIds((prev) => {
          if (!prev.has(o.id)) return prev
          const next = new Set(prev)
          next.delete(o.id)
          return next
        })
      }, PULSE_DURATION_MS)

      toast.success(`🔔 Nuevo pedido — ${orderToastSummary(o)}`, {
        duration: TOAST_DURATION_MS,
      })
    })

    if (alertsEnabledRef.current) {
      playChime()
    }
    if (document.hidden) {
      setUnreadCount((c) => c + newOnes.length)
    }
  }, [])

  const streamUrl = useMemo(() => {
    const params = new URLSearchParams({
      businessId,
      from: range.from,
      to: range.to,
    })
    return `/api/orders/stream?${params.toString()}`
  }, [businessId, range.from, range.to])

  const onSnapshot = useCallback(
    (next: OrderRow[]) => {
      // First snapshot is the initial backlog — seed the seen-set without
      // firing alerts. Every snapshot after that is the diff source.
      if (seenIdsRef.current === null) {
        seenIdsRef.current = new Set(next.map((o) => o.id))
      } else {
        const seen = seenIdsRef.current
        const newOnes = next.filter((o) => !seen.has(o.id))
        newOnes.forEach((o) => seen.add(o.id))
        if (newOnes.length > 0) {
          handleNewOrders(newOnes)
        }
      }

      const pending = pendingStatusRef.current
      if (pending.size === 0) {
        setOrders(next)
        return
      }
      setOrders(
        next.map((row) => {
          const optimisticStatus = pending.get(row.id)
          return optimisticStatus ? { ...row, status: optimisticStatus } : row
        })
      )
    },
    [handleNewOrders]
  )

  useEventSource<OrderRow[]>(streamUrl, "snapshot", onSnapshot)

  // Reseed local state if the server-rendered initialOrders ever changes
  // (e.g. soft-nav back to the page) without an explicit SSE event.
  useEffect(() => {
    setOrders(initialOrders)
  }, [initialOrders])

  // Range change → reset the new-order detector so the next snapshot
  // (under the new filter) seeds the seen-set instead of firing chimes
  // for every row in the new window.
  useEffect(() => {
    seenIdsRef.current = null
  }, [range.from, range.to])

  const handleActivateAlerts = useCallback(async () => {
    const ok = await unlockAndPlayTest()
    setAudioUnlocked(ok)
    if (ok) {
      setAlertsEnabled(true)
      persistAlertsEnabled(true)
      toast.success("Alertas activadas")
    } else {
      toast.error("No se pudo activar el sonido en este navegador")
    }
  }, [])

  const handleToggleMute = useCallback(async () => {
    if (alertsEnabled) {
      setAlertsEnabled(false)
      persistAlertsEnabled(false)
      return
    }
    // Re-enabling — re-unlock since the AudioContext may have suspended.
    await handleActivateAlerts()
  }, [alertsEnabled, handleActivateAlerts])

  async function handleCancelConfirm(
    orderId: string,
    result: CancelDialogResult,
  ): Promise<void> {
    const order = orders.find((o) => o.id === orderId)
    const cancellationReason = formatStoredReason({
      reasonKey: result.reasonKey,
      otherText: result.otherText,
      notes: result.notes,
    })
    setUpdating(orderId)
    pendingStatusRef.current.set(orderId, "cancelled")
    setOrders((prev) =>
      prev.map((o) => (o.id === orderId ? { ...o, status: "cancelled" } : o))
    )
    try {
      const res = await updateOrderStatus(orderId, "cancelled", {
        cancellationReason,
      })
      if (!res.success) throw new Error(res.error)

      if (result.sendCustomerMessage && order?.whatsapp_id) {
        try {
          const sendRes = await fetch("/api/conversations/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              whatsappId: order.whatsapp_id,
              businessId,
              text: result.previewMessage,
            }),
          })
          if (!sendRes.ok) {
            toast.error(
              "Pedido cancelado, pero no pudimos enviar el mensaje al cliente."
            )
          } else {
            toast.success("Pedido cancelado y mensaje enviado")
          }
        } catch {
          toast.error(
            "Pedido cancelado, pero no pudimos enviar el mensaje al cliente."
          )
        }
      } else {
        toast.success("Pedido cancelado")
      }
    } catch (err) {
      pendingStatusRef.current.delete(orderId)
      toast.error(err instanceof Error ? err.message : "No se pudo cancelar")
      // Re-throw so the dialog stays open for the operator to retry.
      throw err
    } finally {
      pendingStatusRef.current.delete(orderId)
      setUpdating(null)
    }
  }

  async function handleStatusChange(orderId: string, status: OrderStatus) {
    // Time-gate defense in case the Select's disabled attribute is
    // bypassed (e.g. via keyboard / future API). The dropdown should
    // already filter these out — this is just a safety net.
    const order = orders.find((o) => o.id === orderId)
    const gate = canSetStatus(status, order?.created_at ?? null, Date.now())
    if (!gate.allowed) {
      toast.error(
        `Disponible en ${gate.minutesRemaining} min (mínimo ${gate.thresholdMinutes} min desde el pedido).`
      )
      return
    }
    setUpdating(orderId)
    pendingStatusRef.current.set(orderId, status)
    setOrders((prev) =>
      prev.map((o) => (o.id === orderId ? { ...o, status } : o))
    )
    try {
      const result = await updateOrderStatus(orderId, status)
      if (!result.success) throw new Error(result.error)
      toast.success("Estado actualizado")
    } catch (err) {
      // Roll back: drop the pending marker and let the next snapshot win.
      pendingStatusRef.current.delete(orderId)
      toast.error(err instanceof Error ? err.message : "No se pudo actualizar")
    } finally {
      pendingStatusRef.current.delete(orderId)
      setUpdating(null)
    }
  }

  const showActivateBanner = !alertsEnabled
  const audioWillPlay = alertsEnabled && audioUnlocked

  const presets: { key: RangePreset; label: string }[] = [
    { key: "today", label: "Hoy" },
    { key: "yesterday", label: "Ayer" },
    { key: "week", label: "Semana" },
    { key: "month", label: "Mes" },
  ]

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1">
            {presets.map((p) => (
              <Button
                key={p.key}
                type="button"
                size="sm"
                variant={kind === p.key ? "default" : "outline"}
                onClick={() => onPreset(p.key)}
              >
                {p.label}
              </Button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => onShiftDay(-1)}
              aria-label="Día anterior"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <input
              type="date"
              value={range.from}
              max={range.to === range.from ? undefined : range.from}
              onChange={(e) => onPickDate(e.target.value)}
              aria-label="Elegir fecha"
              className="h-9 rounded-md border border-input bg-transparent px-2 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => onShiftDay(1)}
              aria-label="Día siguiente"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>

          <span className="text-sm text-muted-foreground">
            {formatRangeLabel(range, kind)}
          </span>
        </div>

        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant={alertsEnabled ? "secondary" : "outline"}
                size="sm"
                onClick={() => void handleToggleMute()}
                aria-pressed={alertsEnabled}
                className="gap-1.5"
              >
                {alertsEnabled ? (
                  audioWillPlay ? (
                    <BellRing className="h-4 w-4" />
                  ) : (
                    <Bell className="h-4 w-4" />
                  )
                ) : (
                  <BellOff className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">
                  {alertsEnabled ? "Alertas activas" : "Alertas en silencio"}
                </span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {alertsEnabled
                ? "Click para silenciar el sonido"
                : "Click para activar el sonido al llegar pedidos"}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {showActivateBanner && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
          <div className="flex items-center gap-2">
            <Bell className="h-4 w-4 text-primary flex-shrink-0" />
            <span>
              Activa las alertas para escuchar un sonido cuando llegue un
              pedido nuevo.
            </span>
          </div>
          <Button
            size="sm"
            onClick={() => void handleActivateAlerts()}
            className="flex-shrink-0"
          >
            Activar alertas
          </Button>
        </div>
      )}

      <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Fecha / Edad</TableHead>
            <TableHead>Teléfono</TableHead>
            <TableHead>Nombre</TableHead>
            <TableHead>Dirección</TableHead>
            <TableHead>Pago</TableHead>
            <TableHead>Ítems</TableHead>
            <TableHead>Total</TableHead>
            <TableHead>Notas</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead>Acciones</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={11}
                className="text-center text-muted-foreground py-8"
              >
                No hay pedidos en este rango.
              </TableCell>
            </TableRow>
          ) : (
            orders.map((order) => {
              const nextStates = Array.from(adminAllowedNext(order.status))
              // Bot's "terminal" set still gates the edit-items dialog —
              // we don't want admins rewriting completed/cancelled order
              // contents. The status badge itself is always editable.
              const isBotTerminal =
                order.status === "completed" || order.status === "cancelled"
              return (
                <TableRow
                  key={order.id}
                  className={cn(
                    order.status === "pending" && "order-row-pending",
                    recentIds.has(order.id) && "order-row-pulse",
                    // Auto-handoff: customer asked for status >50min after
                    // placement and the bot escalated to a human. Light
                    // red signals "act now" — distinct from the amber
                    // attention banner so it stands out on this table.
                    order.awaiting_handoff &&
                      "bg-red-50 hover:bg-red-100 border-l-4 border-l-red-500 dark:bg-red-950/40 dark:hover:bg-red-950/60"
                  )}
                >
                  <TableCell className="font-mono text-sm font-medium">
                    {formatDisplayNumber(order.display_number)}
                  </TableCell>

                  <TableCell className="text-muted-foreground">
                    {order.created_at
                      ? IN_FLIGHT_STATUSES.has(order.status)
                        ? formatElapsedSince(order.created_at, now)
                        : format(new Date(order.created_at), "MMM d, yyyy HH:mm")
                      : "—"}
                  </TableCell>
                  <TableCell>
                    {order.whatsapp_id ||
                      (order.customer_id
                        ? `Cliente #${order.customer_id}`
                        : "—")}
                  </TableCell>
                  <TableCell>{capitalize(order.customer_name)}</TableCell>
                  <TableCell className="max-w-[220px] whitespace-normal break-words align-top">
                    {order.fulfillment_type === "pickup" ? (
                      <Badge variant="secondary" className="font-normal">
                        🏃 Recoger en local
                      </Badge>
                    ) : (
                      capitalize(order.delivery_address)
                    )}
                  </TableCell>
                  <TableCell>
                    {order.fulfillment_type === "pickup"
                      ? "—"
                      : capitalize(order.payment_method)}
                  </TableCell>
                  <TableCell className="text-sm align-top">
                    {order.items.length > 0
                      ? order.items.map((item) => (
                          <div key={item.id}>
                            <span>
                              {item.quantity}× {item.productName}
                            </span>
                            <span className="text-muted-foreground">
                              {" "}
                              — {formatAmount(item.lineTotal)}
                              {item.quantity > 1 ? (
                                <span className="ml-1 text-xs">
                                  ({formatAmount(item.unitPrice)} c/u)
                                </span>
                              ) : null}
                            </span>
                            {item.notes ? (
                              <div className="text-muted-foreground italic">
                                {item.notes}
                              </div>
                            ) : null}
                          </div>
                        ))
                      : "—"}
                  </TableCell>
                  <TableCell className="align-top">
                    <div className="text-xs text-muted-foreground">
                      Subtotal: {formatAmount(order.subtotal)}
                    </div>
                    {order.fulfillment_type !== "pickup" && (
                      <div className="text-xs text-muted-foreground">
                        Domicilio: {formatAmount(order.delivery_fee)}
                      </div>
                    )}
                    <div className="font-medium">
                      Total: {formatAmount(order.total_amount)}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[220px] whitespace-normal break-words align-top text-sm">
                    {order.notes ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Select
                      value={order.status}
                      onValueChange={(val) => {
                        // Cancellation requires a reason + customer
                        // message confirmation — route through the
                        // dialog instead of straight to the action.
                        if (val === "cancelled") {
                          setCancellingOrderId(order.id)
                          return
                        }
                        void handleStatusChange(order.id, val as OrderStatus)
                      }}
                      disabled={updating === order.id}
                    >
                      <SelectTrigger className="w-36 h-8">
                        <Badge
                          variant={statusVariant(order.status)}
                          className="pointer-events-none"
                        >
                          {updating === order.id
                            ? "..."
                            : labelFor(order.status)}
                        </Badge>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={order.status} disabled>
                          {labelFor(order.status)}
                        </SelectItem>
                        {nextStates.map((s) => {
                          const gate = canSetStatus(s, order.created_at, now)
                          return (
                            <SelectItem
                              key={s}
                              value={s}
                              disabled={!gate.allowed}
                            >
                              {STATUS_LABELS[s]}
                              {!gate.allowed && (
                                <span className="ml-1 text-xs text-muted-foreground">
                                  (en {gate.minutesRemaining} min)
                                </span>
                              )}
                            </SelectItem>
                          )
                        })}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => setEditingOrderId(order.id)}
                        disabled={isBotTerminal}
                        aria-label="Editar pedido"
                        title={
                          isBotTerminal
                            ? "No se puede editar un pedido completado o cancelado"
                            : "Editar pedido"
                        }
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          window.open(
                            `/orders/${order.id}/print`,
                            "_blank",
                            "noopener,noreferrer"
                          )
                        }
                        aria-label="Imprimir pedido"
                      >
                        <Printer className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })
          )}
        </TableBody>
      </Table>
      </div>

      <EditOrderDialog
        orderId={editingOrderId}
        open={editingOrderId !== null}
        onOpenChange={(next) => {
          if (!next) setEditingOrderId(null)
        }}
        products={products}
        customers={customers}
      />

      {cancellingOrderId &&
        (() => {
          const o = orders.find((x) => x.id === cancellingOrderId)
          if (!o) return null
          return (
            <CancelOrderDialog
              open={true}
              onOpenChange={(next) => {
                if (!next) setCancellingOrderId(null)
              }}
              displayNumber={o.display_number}
              customerName={o.customer_name}
              onConfirm={(result) =>
                handleCancelConfirm(cancellingOrderId, result)
              }
            />
          )
        })()}
    </div>
  )
}
