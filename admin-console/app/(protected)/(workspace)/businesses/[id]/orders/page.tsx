import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { redirectIfModuleDisabled } from "@/lib/modules"
import { notFound, redirect } from "next/navigation"
import { OrdersTable } from "./components/orders-table"
import { getOrdersForBusiness } from "@/lib/orders-queries"
import { parseRange, rangeToUtc } from "@/lib/orders-date-range"

interface OrdersPageProps {
  params: Promise<{ id: string }>
  searchParams: Promise<{ from?: string; to?: string }>
}

export default async function OrdersPage({ params, searchParams }: OrdersPageProps) {
  const { id } = await params
  const sp = await searchParams
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }
  await redirectIfModuleDisabled(id, "orders")

  const business = await prisma.businesses.findUnique({ where: { id } })
  if (!business) notFound()

  const range = parseRange({ from: sp.from, to: sp.to })
  const initialOrders = await getOrdersForBusiness(id, rangeToUtc(range))

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
        initialRange={range}
      />
    </div>
  )
}
