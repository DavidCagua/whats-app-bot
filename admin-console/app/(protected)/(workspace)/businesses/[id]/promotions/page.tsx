import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { listPromotions } from "@/lib/actions/promotions"
import { PromotionsManager } from "./components/promotions-manager"

interface PromotionsPageProps {
  params: Promise<{ id: string }>
}

export default async function PromotionsPage({ params }: PromotionsPageProps) {
  const { id } = await params
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({ where: { id } })
  if (!business) notFound()

  const promotions = await listPromotions(id)

  const products = await prisma.products.findMany({
    where: { business_id: id, is_active: true },
    orderBy: [{ name: "asc" }],
    select: { id: true, name: true, price: true },
  })
  const mappedProducts = products.map((p) => ({
    id: p.id,
    name: p.name,
    price: Number(p.price.toString()),
  }))

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Promociones</h2>
        <p className="text-sm text-muted-foreground">
          Promos y combos configurados para {business.name}
        </p>
      </div>

      <PromotionsManager
        businessId={id}
        initialPromotions={promotions}
        products={mappedProducts}
      />
    </div>
  )
}
