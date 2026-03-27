"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export type ServiceInput = {
  name: string
  description?: string | null
  price: number
  currency?: string | null
  duration_minutes: number
}

function servicesPath(businessId: string) {
  return `/businesses/${businessId}/services`
}

export async function createService(businessId: string, data: ServiceInput) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }
  if (!canEditBusiness(session, businessId)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const service = await prisma.services.create({
      data: {
        business_id: businessId,
        name: data.name.trim(),
        description: data.description?.trim() || null,
        price: data.price,
        currency: data.currency?.trim() || "COP",
        duration_minutes: data.duration_minutes,
      },
    })
    revalidatePath(servicesPath(businessId))
    return { success: true as const, service }
  } catch (err) {
    console.error("createService error:", err)
    return { success: false as const, error: "Failed to create service" }
  }
}

export async function updateService(
  serviceId: string,
  data: Partial<ServiceInput>
) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.services.findUnique({ where: { id: serviceId } })
  if (!existing) return { success: false as const, error: "Service not found" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const service = await prisma.services.update({
      where: { id: serviceId },
      data: {
        ...(data.name !== undefined ? { name: data.name.trim() } : {}),
        ...(data.description !== undefined ? { description: data.description?.trim() || null } : {}),
        ...(data.price !== undefined ? { price: data.price } : {}),
        ...(data.currency !== undefined ? { currency: data.currency?.trim() || "COP" } : {}),
        ...(data.duration_minutes !== undefined ? { duration_minutes: data.duration_minutes } : {}),
        updated_at: new Date(),
      },
    })
    revalidatePath(servicesPath(existing.business_id))
    return { success: true as const, service }
  } catch (err) {
    console.error("updateService error:", err)
    return { success: false as const, error: "Failed to update service" }
  }
}

export async function setServiceActive(serviceId: string, isActive: boolean) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.services.findUnique({ where: { id: serviceId } })
  if (!existing) return { success: false as const, error: "Service not found" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const service = await prisma.services.update({
      where: { id: serviceId },
      data: { is_active: isActive, updated_at: new Date() },
    })
    revalidatePath(servicesPath(existing.business_id))
    return { success: true as const, service }
  } catch (err) {
    console.error("setServiceActive error:", err)
    return { success: false as const, error: "Failed to update service status" }
  }
}

export async function getBusinessServices(businessId: string) {
  const session = await auth()
  if (!session?.user) return null
  if (!canAccessBusiness(session, businessId)) return null

  return prisma.services.findMany({
    where: { business_id: businessId },
    orderBy: [{ is_active: "desc" }, { name: "asc" }],
  })
}
