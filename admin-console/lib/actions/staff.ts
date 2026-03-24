"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export async function getStaffMembers(businessId: string) {
  const session = await auth()

  if (!session?.user || !canAccessBusiness(session, businessId)) {
    return []
  }

  try {
    const staff = await prisma.staff_members.findMany({
      where: { business_id: businessId },
      include: {
        users: {
          select: {
            id: true,
            email: true,
            full_name: true,
          },
        },
      },
      orderBy: { name: "asc" },
    })

    return staff.map((s) => ({
      id: s.id,
      name: s.name,
      role: s.role,
      is_active: s.is_active,
      user_id: s.user_id,
      user: s.users
        ? {
            id: s.users.id,
            email: s.users.email,
            full_name: s.users.full_name,
          }
        : null,
      created_at: s.created_at,
    }))
  } catch (error) {
    console.error("Error getting staff members:", error)
    return []
  }
}

export async function createStaffMember(
  businessId: string,
  data: {
    name: string
    role: string
    is_active?: boolean
    user_id?: string | null
  }
) {
  const session = await auth()

  if (!session?.user || !canEditBusiness(session, businessId)) {
    return { success: false, error: "Unauthorized" }
  }

  try {
    const staff = await prisma.staff_members.create({
      data: {
        business_id: businessId,
        name: data.name,
        role: data.role,
        is_active: data.is_active !== false,
        user_id: data.user_id || null,
      },
      include: {
        users: {
          select: {
            id: true,
            email: true,
            full_name: true,
          },
        },
      },
    })

    revalidatePath(`/businesses/${businessId}/staff`)

    return {
      success: true,
      staff: {
        id: staff.id,
        name: staff.name,
        role: staff.role,
        is_active: staff.is_active,
        user_id: staff.user_id,
        user: staff.users
          ? {
              id: staff.users.id,
              email: staff.users.email,
              full_name: staff.users.full_name,
            }
          : null,
      },
    }
  } catch (error) {
    console.error("Error creating staff member:", error)
    return { success: false, error: "Failed to create staff member" }
  }
}

export async function updateStaffMember(
  staffId: string,
  businessId: string,
  data: {
    name?: string
    role?: string
    is_active?: boolean
    user_id?: string | null
  }
) {
  const session = await auth()

  if (!session?.user || !canEditBusiness(session, businessId)) {
    return { success: false, error: "Unauthorized" }
  }

  try {
    // Verify the staff member belongs to this business
    const staff = await prisma.staff_members.findUnique({
      where: { id: staffId },
    })

    if (!staff || staff.business_id !== businessId) {
      return { success: false, error: "Staff member not found" }
    }

    const updated = await prisma.staff_members.update({
      where: { id: staffId },
      data: {
        ...(data.name && { name: data.name }),
        ...(data.role && { role: data.role }),
        ...(data.is_active !== undefined && { is_active: data.is_active }),
        ...(data.user_id !== undefined && { user_id: data.user_id }),
      },
      include: {
        users: {
          select: {
            id: true,
            email: true,
            full_name: true,
          },
        },
      },
    })

    revalidatePath(`/businesses/${businessId}/staff`)

    return {
      success: true,
      staff: {
        id: updated.id,
        name: updated.name,
        role: updated.role,
        is_active: updated.is_active,
        user_id: updated.user_id,
        user: updated.users
          ? {
              id: updated.users.id,
              email: updated.users.email,
              full_name: updated.users.full_name,
            }
          : null,
      },
    }
  } catch (error) {
    console.error("Error updating staff member:", error)
    return { success: false, error: "Failed to update staff member" }
  }
}

export async function deleteStaffMember(staffId: string, businessId: string) {
  const session = await auth()

  if (!session?.user || !canEditBusiness(session, businessId)) {
    return { success: false, error: "Unauthorized" }
  }

  try {
    // Verify the staff member belongs to this business
    const staff = await prisma.staff_members.findUnique({
      where: { id: staffId },
    })

    if (!staff || staff.business_id !== businessId) {
      return { success: false, error: "Staff member not found" }
    }

    await prisma.staff_members.delete({
      where: { id: staffId },
    })

    revalidatePath(`/businesses/${businessId}/staff`)

    return { success: true }
  } catch (error) {
    console.error("Error deleting staff member:", error)
    return { success: false, error: "Failed to delete staff member" }
  }
}

export async function getAvailableUsers(businessId: string) {
  const session = await auth()

  if (!session?.user || !canAccessBusiness(session, businessId)) {
    return []
  }

  try {
    const users = await prisma.users.findMany({
      where: {
        user_businesses: {
          some: {
            business_id: businessId,
          },
        },
      },
      select: {
        id: true,
        email: true,
        full_name: true,
      },
      orderBy: { full_name: "asc" },
    })

    return users
  } catch (error) {
    console.error("Error getting available users:", error)
    return []
  }
}
