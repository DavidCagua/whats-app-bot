import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { format } from "date-fns"

interface OrdersPageProps {
  params: Promise<{ id: string }>
}

export default async function OrdersPage({ params }: OrdersPageProps) {
  const { id } = await params
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({ where: { id } })
  if (!business) notFound()

  const orders = await prisma.orders.findMany({
    where: { business_id: id },
    orderBy: { created_at: "desc" },
    include: {
      order_items: {
        include: { products: true },
      },
    },
  })

  const formatAmount = (value: { toString: () => string }) =>
    new Intl.NumberFormat("es-CO", {
      style: "currency",
      currency: "COP",
      minimumFractionDigits: 0,
    }).format(Number(value.toString()))

  const statusVariant = (status: string | null) => {
    switch (status) {
      case "completed":
        return "default"
      case "pending":
        return "secondary"
      case "cancelled":
        return "destructive"
      default:
        return "outline"
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Orders</h2>
          <p className="text-sm text-muted-foreground">
            Orders for {business.name}
          </p>
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Customer</TableHead>
              <TableHead>Items</TableHead>
              <TableHead>Total</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orders.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                  No orders yet.
                </TableCell>
              </TableRow>
            ) : (
              orders.map((order) => {
                const itemsList = order.order_items.map((oi) => (
                  <div key={oi.id}>{oi.quantity}× {oi.products.name}</div>
                ))
                return (
                  <TableRow key={order.id}>
                    <TableCell className="text-muted-foreground">
                      {order.created_at
                        ? format(new Date(order.created_at), "MMM d, yyyy HH:mm")
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {order.whatsapp_id || (order.customer_id ? `Customer #${order.customer_id}` : "—")}
                    </TableCell>
                    <TableCell className="text-sm">
                      {itemsList.length > 0 ? itemsList : "—"}
                    </TableCell>
                    <TableCell className="font-medium">
                      {formatAmount(order.total_amount)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(order.status)}>
                        {order.status || "pending"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
