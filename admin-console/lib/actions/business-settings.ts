"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

// Type for the settings JSON stored in the database
type BusinessSettingsData = {
  address?: string
  phone?: string
  city?: string
  state?: string
  country?: string
  timezone?: string
  language?: string
  business_hours?: {
    monday: { open: string; close: string }
    tuesday: { open: string; close: string }
    wednesday: { open: string; close: string }
    thursday: { open: string; close: string }
    friday: { open: string; close: string }
    saturday: { open: string; close: string }
    sunday: { open: string; close: string }
  }
  services?: Array<{
    name: string
    price: number
    duration: number
  }>
  payment_methods?: string[]
  promotions?: string[]
  staff?: Array<{
    name: string
    specialties: string[]
  }>
  appointment_settings?: {
    max_concurrent: number
    min_advance_hours: number
    default_duration_minutes: number
  }
  ai_prompt?: string
}

export type BusinessSettings = {
  // Basic Info
  name: string
  business_type: string
  address: string
  phone: string
  city: string
  state: string
  country: string
  timezone: string
  language: string
  
  // Business Hours
  business_hours: {
    monday: { open: string; close: string }
    tuesday: { open: string; close: string }
    wednesday: { open: string; close: string }
    thursday: { open: string; close: string }
    friday: { open: string; close: string }
    saturday: { open: string; close: string }
    sunday: { open: string; close: string }
  }
  
  // Services
  services: Array<{
    name: string
    price: number
    duration: number
  }>
  
  // Payment Methods
  payment_methods: string[]
  
  // Promotions
  promotions: string[]
  
  // Staff
  staff: Array<{
    name: string
    specialties: string[]
  }>
  
  // Appointment Settings
  appointment_settings: {
    max_concurrent: number
    min_advance_hours: number
    default_duration_minutes: number
  }
  
  // AI Prompt
  ai_prompt: string
}

export async function getBusinessSettings(businessId: string): Promise<BusinessSettings | null> {
  try {
    // Check authentication and authorization
    const session = await auth()
    if (!session?.user) {
      throw new Error("Unauthorized")
    }

    if (!canAccessBusiness(session, businessId)) {
      throw new Error("Access denied to this business")
    }

    const business = await prisma.businesses.findUnique({
      where: { id: businessId },
    })

    if (!business) {
      return null
    }

    const settings = business.settings as BusinessSettingsData

    return {
      name: business.name,
      business_type: business.business_type || "barberia",
      address: settings?.address || "",
      phone: settings?.phone || "",
      city: settings?.city || "",
      state: settings?.state || "",
      country: settings?.country || "",
      timezone: settings?.timezone || "America/Bogota",
      language: settings?.language || "es-CO",
      business_hours: settings?.business_hours || {
        monday: { open: "09:00", close: "19:00" },
        tuesday: { open: "09:00", close: "19:00" },
        wednesday: { open: "09:00", close: "19:00" },
        thursday: { open: "09:00", close: "19:00" },
        friday: { open: "09:00", close: "19:00" },
        saturday: { open: "09:00", close: "18:00" },
        sunday: { open: "closed", close: "closed" },
      },
      services: settings?.services || [],
      payment_methods: settings?.payment_methods || [],
      promotions: settings?.promotions || [],
      staff: settings?.staff || [],
      appointment_settings: settings?.appointment_settings || {
        max_concurrent: 2,
        min_advance_hours: 1,
        default_duration_minutes: 60,
      },
      ai_prompt: settings?.ai_prompt || "",
    }
  } catch (error) {
    console.error("Error fetching business settings:", error)
    return null
  }
}

export async function updateBusinessSettings(
  businessId: string,
  settings: Partial<BusinessSettings>
) {
  try {
    // Check authentication and authorization
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }

    if (!canEditBusiness(session, businessId)) {
      return { success: false, error: "You don't have permission to edit this business" }
    }

    const currentBusiness = await prisma.businesses.findUnique({
      where: { id: businessId },
    })

    if (!currentBusiness) {
      throw new Error("Business not found")
    }

    const currentSettings = (currentBusiness.settings as BusinessSettingsData) || {}

    // Update business name and type
    const updateData: {
      name?: string
      business_type?: string
      settings?: BusinessSettingsData
      updated_at?: Date
    } = {}
    
    if (settings.name !== undefined) {
      updateData.name = settings.name
    }
    
    if (settings.business_type !== undefined) {
      updateData.business_type = settings.business_type
    }

    // Update settings JSON
    const newSettings = {
      ...currentSettings,
      ...(settings.address !== undefined && { address: settings.address }),
      ...(settings.phone !== undefined && { phone: settings.phone }),
      ...(settings.city !== undefined && { city: settings.city }),
      ...(settings.state !== undefined && { state: settings.state }),
      ...(settings.country !== undefined && { country: settings.country }),
      ...(settings.timezone !== undefined && { timezone: settings.timezone }),
      ...(settings.language !== undefined && { language: settings.language }),
      ...(settings.business_hours !== undefined && { business_hours: settings.business_hours }),
      ...(settings.services !== undefined && { services: settings.services }),
      ...(settings.payment_methods !== undefined && { payment_methods: settings.payment_methods }),
      ...(settings.promotions !== undefined && { promotions: settings.promotions }),
      ...(settings.staff !== undefined && { staff: settings.staff }),
      ...(settings.appointment_settings !== undefined && { appointment_settings: settings.appointment_settings }),
      ...(settings.ai_prompt !== undefined && { ai_prompt: settings.ai_prompt }),
    }

    updateData.settings = newSettings
    updateData.updated_at = new Date()

    await prisma.businesses.update({
      where: { id: businessId },
      data: updateData,
    })

    revalidatePath(`/businesses/${businessId}/settings`)
    
    return { success: true }
  } catch (error) {
    console.error("Error updating business settings:", error)
    return { success: false, error: "Failed to update settings" }
  }
}
