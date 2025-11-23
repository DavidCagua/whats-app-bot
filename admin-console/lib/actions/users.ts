"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin, canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"
import { hash } from "bcryptjs"

export async function getUsers() {
  const session = await auth()

  if (!session?.user || !isSuperAdmin(session)) {
    return []
  }

  const users = await prisma.users.findMany({
    include: {
      user_businesses: {
        include: {
          businesses: true,
        },
      },
    },
    orderBy: { created_at: "desc" },
  })

  return users.map((user) => ({
    id: user.id,
    email: user.email,
    full_name: user.full_name,
    role: user.role,
    is_active: user.is_active,
    created_at: user.created_at,
    businesses: user.user_businesses.map((ub) => ({
      id: ub.business_id,
      name: ub.businesses.name,
      role: ub.role,
    })),
  }))
}

export async function createUser(data: {
  email: string
  password: string
  full_name: string
  role: string | null
}) {
  const session = await auth()

  if (!session?.user || !isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can create users" }
  }

  try {
    // Check if email already exists
    const existingUser = await prisma.users.findUnique({
      where: { email: data.email },
    })

    if (existingUser) {
      return { success: false, error: "A user with this email already exists" }
    }

    const password_hash = await hash(data.password, 12)

    const user = await prisma.users.create({
      data: {
        email: data.email,
        password_hash,
        full_name: data.full_name,
        role: data.role || null,
        is_active: true,
      },
    })

    revalidatePath("/users")

    return { success: true, userId: user.id }
  } catch (error) {
    console.error("Error creating user:", error)
    return { success: false, error: "Failed to create user" }
  }
}

export async function updateUser(
  userId: string,
  data: {
    email?: string
    full_name?: string
    role?: string | null
    is_active?: boolean
    password?: string
  }
) {
  const session = await auth()

  if (!session?.user || !isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can update users" }
  }

  try {
    const updateData: {
      email?: string
      full_name?: string
      role?: string | null
      is_active?: boolean
      password_hash?: string
      updated_at: Date
    } = {
      updated_at: new Date(),
    }

    if (data.email !== undefined) updateData.email = data.email
    if (data.full_name !== undefined) updateData.full_name = data.full_name
    if (data.role !== undefined) updateData.role = data.role
    if (data.is_active !== undefined) updateData.is_active = data.is_active
    if (data.password) {
      updateData.password_hash = await hash(data.password, 12)
    }

    await prisma.users.update({
      where: { id: userId },
      data: updateData,
    })

    revalidatePath("/users")
    revalidatePath(`/users/${userId}`)

    return { success: true }
  } catch (error) {
    console.error("Error updating user:", error)
    return { success: false, error: "Failed to update user" }
  }
}

export async function deleteUser(userId: string) {
  const session = await auth()

  if (!session?.user || !isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can delete users" }
  }

  // Prevent deleting yourself
  if (session.user.id === userId) {
    return { success: false, error: "You cannot delete your own account" }
  }

  try {
    await prisma.users.delete({
      where: { id: userId },
    })

    revalidatePath("/users")

    return { success: true }
  } catch (error) {
    console.error("Error deleting user:", error)
    return { success: false, error: "Failed to delete user" }
  }
}

export async function assignUserToBusiness(
  userId: string,
  businessId: string,
  role: string
) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  // Super admins can assign anyone, business admins can only assign to their business
  const canAssign = isSuperAdmin(session) || canEditBusiness(session, businessId)

  if (!canAssign) {
    return { success: false, error: "You don't have permission to assign users to this business" }
  }

  // Business admins cannot assign super_admin role
  if (!isSuperAdmin(session) && role === "admin") {
    // Business admins can only assign staff role
    role = "staff"
  }

  try {
    // Check if assignment already exists
    const existing = await prisma.user_businesses.findFirst({
      where: {
        user_id: userId,
        business_id: businessId,
      },
    })

    if (existing) {
      // Update existing assignment
      await prisma.user_businesses.update({
        where: { id: existing.id },
        data: { role },
      })
    } else {
      // Create new assignment
      await prisma.user_businesses.create({
        data: {
          user_id: userId,
          business_id: businessId,
          role,
        },
      })
    }

    revalidatePath("/users")
    revalidatePath(`/users/${userId}`)
    revalidatePath(`/businesses/${businessId}/team`)

    return { success: true }
  } catch (error) {
    console.error("Error assigning user to business:", error)
    return { success: false, error: "Failed to assign user to business" }
  }
}

export async function removeUserFromBusiness(userId: string, businessId: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  // Super admins can remove anyone, business admins can only remove from their business
  const canRemove = isSuperAdmin(session) || canEditBusiness(session, businessId)

  if (!canRemove) {
    return { success: false, error: "You don't have permission to remove users from this business" }
  }

  try {
    await prisma.user_businesses.deleteMany({
      where: {
        user_id: userId,
        business_id: businessId,
      },
    })

    revalidatePath("/users")
    revalidatePath(`/users/${userId}`)
    revalidatePath(`/businesses/${businessId}/team`)

    return { success: true }
  } catch (error) {
    console.error("Error removing user from business:", error)
    return { success: false, error: "Failed to remove user from business" }
  }
}

export async function inviteUserToBusiness(
  businessId: string,
  data: {
    email: string
    full_name: string
    password: string
    role: string
  }
) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  // Check if user can invite to this business
  const canInvite = isSuperAdmin(session) || canEditBusiness(session, businessId)

  if (!canInvite) {
    return { success: false, error: "You don't have permission to invite users to this business" }
  }

  try {
    // Check if user already exists
    let user = await prisma.users.findUnique({
      where: { email: data.email },
    })

    if (user) {
      // User exists, just assign to business
      const existing = await prisma.user_businesses.findFirst({
        where: {
          user_id: user.id,
          business_id: businessId,
        },
      })

      if (existing) {
        return { success: false, error: "User is already assigned to this business" }
      }

      await prisma.user_businesses.create({
        data: {
          user_id: user.id,
          business_id: businessId,
          role: data.role,
        },
      })
    } else {
      // Create new user and assign to business
      const password_hash = await hash(data.password, 12)

      user = await prisma.users.create({
        data: {
          email: data.email,
          password_hash,
          full_name: data.full_name,
          role: null, // Not a super admin
          is_active: true,
        },
      })

      await prisma.user_businesses.create({
        data: {
          user_id: user.id,
          business_id: businessId,
          role: data.role,
        },
      })
    }

    revalidatePath(`/businesses/${businessId}/team`)
    revalidatePath("/users")

    return { success: true, userId: user.id }
  } catch (error) {
    console.error("Error inviting user to business:", error)
    return { success: false, error: "Failed to invite user" }
  }
}

export async function getBusinessUsers(businessId: string) {
  const session = await auth()

  if (!session?.user) {
    return []
  }

  // Check access
  const hasAccess =
    isSuperAdmin(session) ||
    session.user.businesses?.some((b) => b.businessId === businessId)

  if (!hasAccess) {
    return []
  }

  const userBusinesses = await prisma.user_businesses.findMany({
    where: { business_id: businessId },
    include: {
      users: true,
    },
  })

  return userBusinesses.map((ub) => ({
    id: ub.users.id,
    email: ub.users.email,
    full_name: ub.users.full_name,
    role: ub.role,
    is_active: ub.users.is_active,
    created_at: ub.created_at,
  }))
}
