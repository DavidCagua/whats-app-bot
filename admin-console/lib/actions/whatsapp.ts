"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export type WhatsAppNumber = {
  id: string
  business_id: string
  phone_number_id: string
  phone_number: string
  display_name: string | null
  is_active: boolean
  created_at: Date
  updated_at: Date
}

/**
 * Get all WhatsApp numbers for a business
 * Only super admins can view WhatsApp numbers
 */
export async function getWhatsAppNumbers(businessId: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized", numbers: [] }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can view WhatsApp numbers", numbers: [] }
  }

  try {
    const numbers = await prisma.whatsapp_numbers.findMany({
      where: { business_id: businessId },
      orderBy: { created_at: "desc" },
    })

    return { success: true, numbers }
  } catch (error) {
    console.error("Error fetching WhatsApp numbers:", error)
    return { success: false, error: "Failed to fetch WhatsApp numbers", numbers: [] }
  }
}

/**
 * Add a new WhatsApp number to a business
 * Only super admins can add WhatsApp numbers
 */
export async function addWhatsAppNumber(data: {
  businessId: string
  phoneNumberId: string
  phoneNumber: string
  displayName?: string
}) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can add WhatsApp numbers" }
  }

  // Validate phone_number_id format (should be numeric, 15-20 digits typically)
  if (!/^\d{10,25}$/.test(data.phoneNumberId)) {
    return {
      success: false,
      error: "Invalid Phone Number ID format. Should be 10-25 digits (e.g., 123456789012345)",
    }
  }

  // Validate phone number format
  if (!/^\+?\d{10,15}$/.test(data.phoneNumber.replace(/[\s-]/g, ""))) {
    return {
      success: false,
      error: "Invalid phone number format. Should be in format +573001234567",
    }
  }

  try {
    // Check if phone_number_id already exists
    const existing = await prisma.whatsapp_numbers.findUnique({
      where: { phone_number_id: data.phoneNumberId },
    })

    if (existing) {
      return {
        success: false,
        error: "This Phone Number ID is already registered to another business",
      }
    }

    // Create the WhatsApp number
    const whatsappNumber = await prisma.whatsapp_numbers.create({
      data: {
        business_id: data.businessId,
        phone_number_id: data.phoneNumberId,
        phone_number: data.phoneNumber,
        display_name: data.displayName || null,
        is_active: true,
      },
    })

    revalidatePath(`/businesses/${data.businessId}/settings`)

    return { success: true, number: whatsappNumber }
  } catch (error) {
    console.error("Error adding WhatsApp number:", error)
    return { success: false, error: "Failed to add WhatsApp number" }
  }
}

/**
 * Update an existing WhatsApp number
 * Only super admins can update WhatsApp numbers
 */
export async function updateWhatsAppNumber(
  id: string,
  data: {
    phoneNumber?: string
    displayName?: string
    isActive?: boolean
  }
) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can update WhatsApp numbers" }
  }

  try {
    const updateData: {
      phone_number?: string
      display_name?: string | null
      is_active?: boolean
      updated_at: Date
    } = {
      updated_at: new Date(),
    }

    if (data.phoneNumber !== undefined) {
      // Validate phone number format
      if (!/^\+?\d{10,15}$/.test(data.phoneNumber.replace(/[\s-]/g, ""))) {
        return {
          success: false,
          error: "Invalid phone number format. Should be in format +573001234567",
        }
      }
      updateData.phone_number = data.phoneNumber
    }

    if (data.displayName !== undefined) {
      updateData.display_name = data.displayName || null
    }

    if (data.isActive !== undefined) {
      updateData.is_active = data.isActive
    }

    const whatsappNumber = await prisma.whatsapp_numbers.update({
      where: { id },
      data: updateData,
    })

    revalidatePath(`/businesses/${whatsappNumber.business_id}/settings`)

    return { success: true, number: whatsappNumber }
  } catch (error) {
    console.error("Error updating WhatsApp number:", error)
    return { success: false, error: "Failed to update WhatsApp number" }
  }
}

/**
 * Delete a WhatsApp number
 * Only super admins can delete WhatsApp numbers
 */
export async function deleteWhatsAppNumber(id: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can delete WhatsApp numbers" }
  }

  try {
    const whatsappNumber = await prisma.whatsapp_numbers.findUnique({
      where: { id },
      select: { business_id: true },
    })

    if (!whatsappNumber) {
      return { success: false, error: "WhatsApp number not found" }
    }

    await prisma.whatsapp_numbers.delete({
      where: { id },
    })

    revalidatePath(`/businesses/${whatsappNumber.business_id}/settings`)

    return { success: true }
  } catch (error) {
    console.error("Error deleting WhatsApp number:", error)
    return { success: false, error: "Failed to delete WhatsApp number" }
  }
}

/**
 * Toggle active status of a WhatsApp number
 * Only super admins can toggle WhatsApp number status
 */
export async function toggleWhatsAppNumberStatus(id: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!isSuperAdmin(session)) {
    return { success: false, error: "Only super admins can modify WhatsApp numbers" }
  }

  try {
    const whatsappNumber = await prisma.whatsapp_numbers.findUnique({
      where: { id },
    })

    if (!whatsappNumber) {
      return { success: false, error: "WhatsApp number not found" }
    }

    const updated = await prisma.whatsapp_numbers.update({
      where: { id },
      data: {
        is_active: !whatsappNumber.is_active,
        updated_at: new Date(),
      },
    })

    revalidatePath(`/businesses/${updated.business_id}/settings`)

    return { success: true, number: updated }
  } catch (error) {
    console.error("Error toggling WhatsApp number status:", error)
    return { success: false, error: "Failed to toggle status" }
  }
}
