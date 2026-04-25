"use client"

import { useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { format } from "date-fns"
import { toast } from "sonner"
import { updateOrderStatus } from "@/lib/actions/orders"
import {
  type OrderStatus,
  STATUS_LABELS,
  allowedNext,
  isValidStatus,
} from "@/lib/order-status"

type OrderItem = {
  id: string
  quantity: number
  productName: string
  notes: string | null
}

type OrderRow = {
  id: string
  created_at: string | null
  whatsapp_id: string | null
  customer_id: number | null
  total_amount: number
  status: string
  items: OrderItem[]
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

export function OrdersTable({ initialOrders }: { initialOrders: OrderRow[] }) {
  const [orders, setOrders] = useState<OrderRow[]>(initialOrders)
  const [updating, setUpdating] = useState<string | null>(null)

  async function handleStatusChange(orderId: string, status: OrderStatus) {
    setUpdating(orderId)
    try {
      const result = await updateOrderStatus(orderId, status)
      if (!result.success) throw new Error(result.error)
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId ? { ...o, status } : o))
      )
      toast.success("Estado actualizado")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "No se pudo actualizar")
    } finally {
      setUpdating(null)
    }
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Fecha</TableHead>
            <TableHead>Cliente</TableHead>
            <TableHead>Ítems</TableHead>
            <TableHead>Total</TableHead>
            <TableHead>Estado</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={6}
                className="text-center text-muted-foreground py-8"
              >
                Aún no hay pedidos.
              </TableCell>
            </TableRow>
          ) : (
            orders.map((order) => {
              const nextStates = Array.from(allowedNext(order.status))
              const isTerminal = nextStates.length === 0
              return (
                <TableRow key={order.id}>
                  <TableCell className="font-mono text-xs">
                    {order.id.slice(0, 8)}
                  </TableCell>

                  <TableCell className="text-muted-foreground">
                    {order.created_at
                      ? format(new Date(order.created_at), "MMM d, yyyy HH:mm")
                      : "—"}
                  </TableCell>
                  <TableCell>
                    {order.whatsapp_id ||
                      (order.customer_id
                        ? `Cliente #${order.customer_id}`
                        : "—")}
                  </TableCell>
                  <TableCell className="text-sm">
                    {order.items.length > 0
                      ? order.items.map((item) => (
                          <div key={item.id}>
                            {item.quantity}× {item.productName}
                            {item.notes ? (
                              <span className="text-muted-foreground italic">
                                {" "}
                                — {item.notes}
                              </span>
                            ) : null}
                          </div>
                        ))
                      : "—"}
                  </TableCell>
                  <TableCell className="font-medium">
                    {formatAmount(order.total_amount)}
                  </TableCell>
                  <TableCell>
                    {isTerminal ? (
                      <Badge variant={statusVariant(order.status)}>
                        {labelFor(order.status)}
                      </Badge>
                    ) : (
                      <Select
                        value={order.status}
                        onValueChange={(val) =>
                          void handleStatusChange(order.id, val as OrderStatus)
                        }
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
                          {/* current status (disabled) + every legal next state */}
                          <SelectItem value={order.status} disabled>
                            {labelFor(order.status)}
                          </SelectItem>
                          {nextStates.map((s) => (
                            <SelectItem key={s} value={s}>
                              {STATUS_LABELS[s]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </TableCell>
                </TableRow>
              )
            })
          )}
        </TableBody>
      </Table>
    </div>
  )
}
