"use server";

import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions";
import { revalidatePath } from "next/cache";

// Type for the settings JSON stored in the database
type BusinessSettingsData = {
  address?: string;
  phone?: string;
  city?: string;
  state?: string;
  country?: string;
  timezone?: string;
  language?: string;
  payment_methods?: string[];
  /** Enlace de pago (Stripe, Mercado Pago, etc.) para el agente de ventas */
  payment_link?: string;
  ai_prompt?: string;
  products_enabled?: boolean;
  menu_url?: string;
  agent_enabled?: boolean;
  /** Si está definido y el agente está habilitado, recibe todos los mensajes (anula el orden solo por prioridad). */
  conversation_primary_agent?: string;
};

export type BusinessSettings = {
  // Basic Info
  name: string;
  business_type: string;
  address: string;
  phone: string;
  city: string;
  state: string;
  country: string;
  timezone: string;
  language: string;

  // Payment Methods
  payment_methods: string[];
  payment_link: string;

  // AI Prompt
  ai_prompt: string;

  // Products / Orders
  products_enabled: boolean;

  // Menu
  menu_url: string;

  // Agent master switch
  agent_enabled: boolean;

  /** "" = primer agente por prioridad; ej. "sales" para tiendas */
  conversation_primary_agent: string;
};

export async function getBusinessSettings(
  businessId: string,
): Promise<BusinessSettings | null> {
  try {
    // Check authentication and authorization
    const session = await auth();
    if (!session?.user) {
      throw new Error("Unauthorized");
    }

    if (!canAccessBusiness(session, businessId)) {
      throw new Error("Access denied to this business");
    }

    const business = await prisma.businesses.findUnique({
      where: { id: businessId },
    });

    if (!business) {
      return null;
    }

    const settings = business.settings as BusinessSettingsData;

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
      payment_methods: settings?.payment_methods || [],
      payment_link: settings?.payment_link ?? "",
      ai_prompt: settings?.ai_prompt || "",
      products_enabled: settings?.products_enabled ?? true,
      menu_url: settings?.menu_url ?? "",
      agent_enabled: settings?.agent_enabled ?? true,
      conversation_primary_agent: settings?.conversation_primary_agent ?? "",
    };
  } catch (error) {
    console.error("Error fetching business settings:", error);
    return null;
  }
}

export async function updateBusinessSettings(
  businessId: string,
  settings: Partial<BusinessSettings>,
) {
  try {
    // Check authentication and authorization
    const session = await auth();
    if (!session?.user) {
      return { success: false, error: "Unauthorized" };
    }

    if (!canEditBusiness(session, businessId)) {
      return {
        success: false,
        error: "You don't have permission to edit this business",
      };
    }

    const currentBusiness = await prisma.businesses.findUnique({
      where: { id: businessId },
    });

    if (!currentBusiness) {
      throw new Error("Business not found");
    }

    const currentSettings =
      (currentBusiness.settings as BusinessSettingsData) || {};

    // Update business name and type
    const updateData: {
      name?: string;
      business_type?: string;
      settings?: BusinessSettingsData;
      updated_at?: Date;
    } = {};

    if (settings.name !== undefined) {
      updateData.name = settings.name;
    }

    if (settings.business_type !== undefined) {
      updateData.business_type = settings.business_type;
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
      ...(settings.payment_methods !== undefined && {
        payment_methods: settings.payment_methods,
      }),
      ...(settings.payment_link !== undefined && {
        payment_link: settings.payment_link,
      }),
      ...(settings.ai_prompt !== undefined && {
        ai_prompt: settings.ai_prompt,
      }),
      ...(settings.products_enabled !== undefined && {
        products_enabled: settings.products_enabled,
      }),
      ...(settings.menu_url !== undefined && { menu_url: settings.menu_url }),
      ...(settings.agent_enabled !== undefined && {
        agent_enabled: settings.agent_enabled,
      }),
      ...(settings.conversation_primary_agent !== undefined && {
        conversation_primary_agent: settings.conversation_primary_agent,
      }),
    };

    updateData.settings = newSettings;
    updateData.updated_at = new Date();

    await prisma.businesses.update({
      where: { id: businessId },
      data: updateData,
    });

    revalidatePath(`/businesses/${businessId}/settings`);

    return { success: true };
  } catch (error) {
    console.error("Error updating business settings:", error);
    return { success: false, error: "Failed to update settings" };
  }
}
