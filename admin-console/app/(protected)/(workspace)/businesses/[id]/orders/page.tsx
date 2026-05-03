import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { OrdersTable } from "./components/orders-table"
import { getOrdersForBusiness } from "@/lib/orders-queries"

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

  const initialOrders = await getOrdersForBusiness(id)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Pedidos</h2>
          <p className="text-sm text-muted-foreground">Pedidos de {business.name}</p>
        </div>
      </div>

      <OrdersTable
        businessId={id}
        businessName={business.name}
        initialOrders={initialOrders}
      />
    </div>
  )
}
