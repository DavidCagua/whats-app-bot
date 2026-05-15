"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"
import { Prisma } from "@prisma/client"

export type UpdateOrderItemInput = {
  productId: string
  quantity: number
  unitPrice: number
  notes?: string | null
}

export type UpdateOrderInput = {
  orderId: string
  customer:
    | { existingCustomerId: number }
    | { whatsappId: string; name: string }
    | null
  items: UpdateOrderItemInput[]
  fulfillmentType?: "delivery" | "pickup"
  deliveryAddress?: string | null
  contactPhone?: string | null
  paymentMethod?: string | null
  deliveryFee?: number
  notes?: string | null
}

type Result =
  | { success: true; orderId: string }
  | { success: false; error: string }

const WHATSAPP_ID_RE = /^[0-9+]{7,30}$/
const TERMINAL_STATUSES = new Set(["completed", "cancelled"])

export async function updateOrder(input: UpdateOrderInput): Promise<Result> {
  const session = await auth()
  if (!session?.user) return { success: false, error: "Unauthorized" }

  const existing = await prisma.orders.findUnique({
    where: { id: input.orderId },
    select: { id: true, business_id: true, status: true },
  })
  if (!existing) return { success: false, error: "Pedido no encontrado" }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false, error: "Forbidden" }
  }
  if (TERMINAL_STATUSES.has(existing.status ?? "")) {
    return {
      success: false,
      error: "No se puede editar un pedido completado o cancelado",
    }
  }

  if (!input.items.length) {
    return { success: false, error: "Agrega al menos un ítem" }
  }
  for (const item of input.items) {
    if (!item.productId) return { success: false, error: "Ítem sin producto" }
    if (!Number.isFinite(item.quantity) || item.quantity <= 0) {
      return { success: false, error: "Cantidad inválida en un ítem" }
    }
    if (!Number.isFinite(item.unitPrice) || item.unitPrice < 0) {
      return { success: false, error: "Precio inválido en un ítem" }
    }
  }

  const fulfillmentType: "delivery" | "pickup" =
    input.fulfillmentType === "pickup" ? "pickup" : "delivery"
  const isPickup = fulfillmentType === "pickup"

  const deliveryFee = isPickup
    ? 0
    : Number.isFinite(input.deliveryFee)
      ? Math.max(0, input.deliveryFee as number)
      : 0

  // Re-validate every product against this business — never trust the client.
  const productIds = Array.from(new Set(input.items.map((i) => i.productId)))
  const products = await prisma.products.findMany({
    where: {
      id: { in: productIds },
      business_id: existing.business_id,
      is_active: true,
    },
    select: { id: true },
  })
  if (products.length !== productIds.length) {
    return {
      success: false,
      error: "Algún producto ya no está disponible o no pertenece al negocio",
    }
  }

  // Resolve customer (existing / new / anonymous), same shape as createOrder.
  let resolvedCustomerId: number | null = null
  let resolvedWhatsappId: string | null = null

  if (input.customer && "existingCustomerId" in input.customer) {
    const c = await prisma.customers.findUnique({
      where: { id: input.customer.existingCustomerId },
      select: { id: true, whatsapp_id: true },
    })
    if (!c) return { success: false, error: "Cliente no encontrado" }
    resolvedCustomerId = c.id
    resolvedWhatsappId = c.whatsapp_id
  } else if (input.customer && "whatsappId" in input.customer) {
    const whatsappId = input.customer.whatsappId.trim()
    const name = input.customer.name.trim()
    if (!whatsappId || !WHATSAPP_ID_RE.test(whatsappId)) {
      return {
        success: false,
        error: "WhatsApp inválido — usa solo dígitos (con + opcional)",
      }
    }
    if (!name) return { success: false, error: "Nombre del cliente requerido" }
    resolvedWhatsappId = whatsappId
    const c = await prisma.customers.upsert({
      where: { whatsapp_id: whatsappId },
      create: { whatsapp_id: whatsappId, name },
      update: {},
    })
    resolvedCustomerId = c.id
  }

  const subtotal = input.items.reduce(
    (acc, i) => acc + i.quantity * i.unitPrice,
    0
  )
  const totalAmount = Number((subtotal + deliveryFee).toFixed(2))

  try {
    await prisma.$transaction(async (tx) => {
      // Strip promotion records — items themselves are kept (admin re-confirmed
      // their prices in the dialog). The promo audit trail goes; the products do not.
      await tx.order_promotions.deleteMany({
        where: { order_id: input.orderId },
      })
      await tx.order_items.deleteMany({
        where: { order_id: input.orderId },
      })

      if (resolvedCustomerId !== null) {
        await tx.business_customers.upsert({
          where: {
            business_id_customer_id: {
              business_id: existing.business_id,
              customer_id: resolvedCustomerId,
            },
          },
          create: {
            business_id: existing.business_id,
            customer_id: resolvedCustomerId,
            source: "auto",
          },
          update: {},
        })
      }

      await tx.orders.update({
        where: { id: input.orderId },
        data: {
          customer_id: resolvedCustomerId,
          whatsapp_id: resolvedWhatsappId,
          total_amount: new Prisma.Decimal(totalAmount.toFixed(2)),
          promo_discount_amount: new Prisma.Decimal("0"),
          notes: input.notes?.trim() || null,
          fulfillment_type: fulfillmentType,
          delivery_address: isPickup ? null : input.deliveryAddress?.trim() || null,
          contact_phone: input.contactPhone?.trim() || null,
          payment_method: isPickup ? null : input.paymentMethod?.trim() || null,
          updated_at: new Date(),
          order_items: {
            create: input.items.map((i) => ({
              product_id: i.productId,
              quantity: i.quantity,
              unit_price: new Prisma.Decimal(i.unitPrice.toFixed(2)),
              line_total: new Prisma.Decimal(
                (i.quantity * i.unitPrice).toFixed(2)
              ),
              notes: i.notes?.trim() || null,
            })),
          },
        },
      })
    })

    revalidatePath(`/businesses/${existing.business_id}/orders`)
    return { success: true, orderId: input.orderId }
  } catch (err) {
    return {
      success: false,
      error:
        err instanceof Error ? err.message : "No se pudo actualizar el pedido",
    }
  }
}
