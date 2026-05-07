"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

type CreateCustomerInput = {
  businessId: string
  whatsappId: string
  name: string
  phone?: string | null
  address?: string | null
  paymentMethod?: string | null
}

type ActionResult =
  | { success: true; customerId: number }
  | { success: false; error: string }

const WHATSAPP_ID_RE = /^[0-9+]{7,30}$/

export async function createCustomer(
  input: CreateCustomerInput
): Promise<ActionResult> {
  const session = await auth()
  if (!session?.user) return { success: false, error: "Unauthorized" }
  if (!canEditBusiness(session, input.businessId)) {
    return { success: false, error: "Forbidden" }
  }

  const whatsappId = input.whatsappId.trim()
  const name = input.name.trim()
  const phone = input.phone?.trim() || null
  const address = input.address?.trim() || null
  const paymentMethod = input.paymentMethod?.trim() || null

  if (!whatsappId) return { success: false, error: "WhatsApp ID requerido" }
  if (!WHATSAPP_ID_RE.test(whatsappId)) {
    return {
      success: false,
      error: "WhatsApp ID inválido — usa solo dígitos (con + opcional)",
    }
  }
  if (!name) return { success: false, error: "Nombre requerido" }

  const business = await prisma.businesses.findUnique({
    where: { id: input.businessId },
    select: { id: true },
  })
  if (!business) return { success: false, error: "Negocio no encontrado" }

  // Two writes: upsert the global customer (canonical identity by
  // whatsapp_id) then upsert the per-business join row. Done in a
  // transaction so a partial state (customer without business link)
  // can't leak when the second write fails.
  const customer = await prisma.$transaction(async (tx) => {
    const c = await tx.customers.upsert({
      where: { whatsapp_id: whatsappId },
      create: {
        whatsapp_id: whatsappId,
        name,
        phone,
        address,
        payment_method: paymentMethod,
      },
      update: {},
    })

    await tx.business_customers.upsert({
      where: {
        business_id_customer_id: {
          business_id: input.businessId,
          customer_id: c.id,
        },
      },
      create: {
        business_id: input.businessId,
        customer_id: c.id,
        name,
        phone,
        address,
        payment_method: paymentMethod,
        source: "manual",
      },
      update: {
        name,
        phone,
        address,
        payment_method: paymentMethod,
        updated_at: new Date(),
      },
    })

    return c
  })

  revalidatePath(`/businesses/${input.businessId}/customers`)
  return { success: true, customerId: customer.id }
}
