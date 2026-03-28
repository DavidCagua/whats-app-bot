import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { ProductsManager } from "./components/products-manager"

interface ProductsPageProps {
  params: Promise<{ id: string }>
}

export default async function ProductsPage({ params }: ProductsPageProps) {
  const { id } = await params
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({ where: { id } })
  if (!business) notFound()

  const products = await prisma.products.findMany({
    where: { business_id: id },
    orderBy: [{ is_active: "desc" }, { name: "asc" }],
  })

  const mappedProducts = products.map((p) => ({
    id: p.id,
    business_id: p.business_id,
    name: p.name,
    description: p.description ?? null,
    sku: p.sku ?? null,
    category: p.category ?? null,
    price: Number(p.price.toString()),
    is_active: p.is_active ?? true,
  }))

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Productos</h2>
        <p className="text-sm text-muted-foreground">
          Catálogo de productos de {business.name}
        </p>
      </div>

      <ProductsManager businessId={id} initialProducts={mappedProducts} />
    </div>
  )
}
