import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { OrdersTable } from "./components/orders-table"

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

  const mappedOrders = orders.map((order) => ({
    id: order.id,
    created_at: order.created_at ? order.created_at.toISOString() : null,
    whatsapp_id: order.whatsapp_id ?? null,
    customer_id: order.customer_id ?? null,
    total_amount: Number(order.total_amount.toString()),
    status: order.status ?? "pending",
    items: order.order_items.map((oi) => ({
      id: oi.id,
      quantity: oi.quantity,
      productName: oi.products.name,
      notes: oi.notes ?? null,
    })),
  }))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Pedidos</h2>
          <p className="text-sm text-muted-foreground">Pedidos de {business.name}</p>
        </div>
      </div>

      <OrdersTable initialOrders={mappedOrders} />
    </div>
  )
}
