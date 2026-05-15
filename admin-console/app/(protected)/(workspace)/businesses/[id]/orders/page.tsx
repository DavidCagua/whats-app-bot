import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions"
import { redirectIfModuleDisabled } from "@/lib/modules"
import { notFound, redirect } from "next/navigation"
import { OrdersTable } from "./components/orders-table"
import { CreateOrderDialog } from "./components/create-order-dialog"
import { OperationsControls } from "./components/operations-controls"
import { getOrdersForBusiness } from "@/lib/orders-queries"
import { getCreateOrderData } from "@/lib/orders-create-data"
import { parseRange, rangeToUtc } from "@/lib/orders-date-range"
import { getOperationsSettings } from "@/lib/actions/operations-settings"

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
  const [initialOrders, createData, opsSettings] = await Promise.all([
    getOrdersForBusiness(id, rangeToUtc(range)),
    getCreateOrderData(id),
    getOperationsSettings(id),
  ])

  const canEdit = canEditBusiness(session, id)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Pedidos</h2>
          <p className="text-sm text-muted-foreground">Pedidos de {business.name}</p>
        </div>
        <CreateOrderDialog
          businessId={id}
          products={createData.products}
          customers={createData.customers}
        />
      </div>

      <OperationsControls
        businessId={id}
        initialDeliveryPaused={opsSettings?.delivery_paused ?? false}
        initialEtaMinutes={opsSettings?.delivery_eta_minutes ?? null}
        canEdit={canEdit}
      />

      <OrdersTable
        businessId={id}
        businessName={business.name}
        initialOrders={initialOrders}
        initialRange={range}
        products={createData.products}
        customers={createData.customers}
      />
    </div>
  )
}
