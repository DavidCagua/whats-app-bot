"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export async function createStaffMember(
  businessId: string,
  data: {
    name: string
    role: string
    is_active: boolean
    user_id: string | null
  }
) {
  try {
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }

    if (!canEditBusiness(session, businessId)) {
      return { success: false, error: "Access denied" }
    }

    const staff = await prisma.staff_members.create({
      data: {
        business_id: businessId,
        name: data.name,
        role: data.role,
        is_active: data.is_active,
        user_id: data.user_id,
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
    return { success: true, staff }
  } catch (error) {
    console.error("Error creating staff member:", error)
    return { success: false, error: "Failed to create staff member" }
  }
}

export async function updateStaffMember(
  staffId: string,
  data: {
    name?: string
    role?: string
    is_active?: boolean
    user_id?: string | null
  }
) {
  try {
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }

    const staffMember = await prisma.staff_members.findUnique({
      where: { id: staffId },
    })

    if (!staffMember) {
      return { success: false, error: "Staff member not found" }
    }

    if (!canEditBusiness(session, staffMember.business_id)) {
      return { success: false, error: "Access denied" }
    }

    const updated = await prisma.staff_members.update({
      where: { id: staffId },
      data: {
        ...(data.name !== undefined && { name: data.name }),
        ...(data.role !== undefined && { role: data.role }),
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

    revalidatePath(`/businesses/${staffMember.business_id}/staff`)
    return { success: true, staff: updated }
  } catch (error) {
    console.error("Error updating staff member:", error)
    return { success: false, error: "Failed to update staff member" }
  }
}

export async function deleteStaffMember(staffId: string) {
  try {
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }

    const staffMember = await prisma.staff_members.findUnique({
      where: { id: staffId },
    })

    if (!staffMember) {
      return { success: false, error: "Staff member not found" }
    }

    if (!canEditBusiness(session, staffMember.business_id)) {
      return { success: false, error: "Access denied" }
    }

    await prisma.staff_members.delete({
      where: { id: staffId },
    })

    revalidatePath(`/businesses/${staffMember.business_id}/staff`)
    return { success: true }
  } catch (error) {
    console.error("Error deleting staff member:", error)
    return { success: false, error: "Failed to delete staff member" }
  }
}

export async function toggleStaffActive(staffId: string, isActive: boolean) {
  try {
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }

    const staffMember = await prisma.staff_members.findUnique({
      where: { id: staffId },
    })

    if (!staffMember) {
      return { success: false, error: "Staff member not found" }
    }

    if (!canEditBusiness(session, staffMember.business_id)) {
      return { success: false, error: "Access denied" }
    }

    await prisma.staff_members.update({
      where: { id: staffId },
      data: { is_active: isActive },
    })

    revalidatePath(`/businesses/${staffMember.business_id}/staff`)
    return { success: true }
  } catch (error) {
    console.error("Error toggling staff active:", error)
    return { success: false, error: "Failed to update" }
  }
}
