"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export async function createBusiness(data: {
  name: string
  business_type: string
}) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can create businesses" }
  }

  try {
    const business = await prisma.businesses.create({
      data: {
        name: data.name,
        business_type: data.business_type,
        settings: {},
        is_active: true,
      },
    })

    revalidatePath("/businesses")

    return { success: true, businessId: business.id }
  } catch (error) {
    console.error("Error creating business:", error)
    return { success: false, error: "Failed to create business" }
  }
}

export async function deleteBusiness(businessId: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can delete businesses" }
  }

  try {
    await prisma.businesses.delete({
      where: { id: businessId },
    })

    revalidatePath("/businesses")

    return { success: true }
  } catch (error) {
    console.error("Error deleting business:", error)
    return { success: false, error: "Failed to delete business" }
  }
}

export async function getBusinesses() {
  const session = await auth()

  if (!session?.user) {
    return []
  }

  if (isSuperAdmin(session)) {
    return prisma.businesses.findMany({
      orderBy: { created_at: "desc" },
    })
  }

  // For non-super admins, get only their businesses
  const businessIds = session.user.businesses?.map((b) => b.businessId) || []

  return prisma.businesses.findMany({
    where: { id: { in: businessIds } },
    orderBy: { created_at: "desc" },
  })
}
