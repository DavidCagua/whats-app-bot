import { prisma } from "./prisma"

export type ProductOption = {
  id: string
  name: string
  price: number
  category: string | null
}

export type CustomerOption = {
  id: number
  name: string
  whatsapp_id: string
}

export type CreateOrderData = {
  products: ProductOption[]
  customers: CustomerOption[]
}

/**
 * Fetches the lookup data the "Crear pedido" dialog needs: active
 * products for the business, plus customers already linked to it via
 * the business_customers join table.
 *
 * Both lists are static enough that doing this once on page render is
 * fine for now — revisit with pagination/search if Biela's catalogue
 * or customer count grows past a few hundred.
 */
export async function getCreateOrderData(
  businessId: string
): Promise<CreateOrderData> {
  const [products, links] = await Promise.all([
    prisma.products.findMany({
      where: { business_id: businessId, is_active: true },
      orderBy: [{ category: "asc" }, { name: "asc" }],
      select: { id: true, name: true, price: true, category: true },
    }),
    prisma.business_customers.findMany({
      where: { business_id: businessId },
      include: {
        customers: { select: { id: true, name: true, whatsapp_id: true } },
      },
      orderBy: { updated_at: "desc" },
    }),
  ])

  return {
    products: products.map((p) => ({
      id: p.id,
      name: p.name,
      price: Number(p.price.toString()),
      category: p.category,
    })),
    customers: links.map((l) => ({
      id: l.customers.id,
      name: l.name ?? l.customers.name,
      whatsapp_id: l.customers.whatsapp_id,
    })),
  }
}
