"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export type ProductInput = {
  name: string
  description?: string | null
  sku?: string | null
  price: number
  category?: string | null
}

export type SerializedProduct = {
  id: string
  business_id: string
  name: string
  description: string | null
  sku: string | null
  category: string | null
  price: number
  is_active: boolean
}

function serialize(p: {
  id: string
  business_id: string
  name: string
  description?: string | null
  sku: string | null
  category?: string | null
  price: { toString(): string }
  is_active: boolean | null
}): SerializedProduct {
  return {
    id: p.id,
    business_id: p.business_id,
    name: p.name,
    description: p.description ?? null,
    sku: p.sku ?? null,
    category: p.category ?? null,
    price: Number(p.price.toString()),
    is_active: p.is_active ?? true,
  }
}

function productsPath(businessId: string) {
  return `/businesses/${businessId}/products`
}

export async function createProduct(businessId: string, data: ProductInput) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }
  if (!canEditBusiness(session, businessId)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const product = await prisma.products.create({
      data: {
        business_id: businessId,
        name: data.name.trim(),
        description: data.description?.trim() || null,
        sku: data.sku?.trim() || null,
        price: data.price,
        category: data.category?.trim() || null,
      },
    })
    revalidatePath(productsPath(businessId))
    return { success: true as const, product: serialize(product) }
  } catch (err) {
    console.error("createProduct error:", err)
    return { success: false as const, error: "Failed to create product" }
  }
}

export async function updateProduct(productId: string, data: Partial<ProductInput>) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.products.findUnique({ where: { id: productId } })
  if (!existing) return { success: false as const, error: "Product not found" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const product = await prisma.products.update({
      where: { id: productId },
      data: {
        ...(data.name !== undefined ? { name: data.name.trim() } : {}),
        ...(data.description !== undefined
          ? { description: data.description?.trim() || null }
          : {}),
        ...(data.sku !== undefined ? { sku: data.sku?.trim() || null } : {}),
        ...(data.price !== undefined ? { price: data.price } : {}),
        ...(data.category !== undefined ? { category: data.category?.trim() || null } : {}),
        updated_at: new Date(),
      },
    })
    revalidatePath(productsPath(existing.business_id))
    return { success: true as const, product: serialize(product) }
  } catch (err) {
    console.error("updateProduct error:", err)
    return { success: false as const, error: "Failed to update product" }
  }
}

export async function setProductActive(productId: string, isActive: boolean) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.products.findUnique({ where: { id: productId } })
  if (!existing) return { success: false as const, error: "Product not found" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const product = await prisma.products.update({
      where: { id: productId },
      data: { is_active: isActive, updated_at: new Date() },
    })
    revalidatePath(productsPath(existing.business_id))
    return { success: true as const, product: serialize(product) }
  } catch (err) {
    console.error("setProductActive error:", err)
    return { success: false as const, error: "Failed to update product status" }
  }
}
