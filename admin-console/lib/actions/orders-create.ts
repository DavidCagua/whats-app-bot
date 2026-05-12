"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"
import { Prisma } from "@prisma/client"

export type CreateOrderItemInput = {
  productId: string
  quantity: number
  unitPrice: number
  notes?: string | null
}

export type CreateOrderInput = {
  businessId: string
  /** Either an existing customer id, or a new whatsapp+name pair, or null for anonymous. */
  customer:
    | { existingCustomerId: number }
    | { whatsappId: string; name: string }
    | null
  items: CreateOrderItemInput[]
  fulfillmentType?: "delivery" | "pickup"
  deliveryAddress?: string | null
  contactPhone?: string | null
  paymentMethod?: string | null
  deliveryFee?: number
  notes?: string | null
}

type ActionResult =
  | { success: true; orderId: string }
  | { success: false; error: string }

const WHATSAPP_ID_RE = /^[0-9+]{7,30}$/

export async function createOrder(
  input: CreateOrderInput
): Promise<ActionResult> {
  const session = await auth()
  if (!session?.user) return { success: false, error: "Unauthorized" }
  if (!canEditBusiness(session, input.businessId)) {
    return { success: false, error: "Forbidden" }
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

  const business = await prisma.businesses.findUnique({
    where: { id: input.businessId },
    select: { id: true },
  })
  if (!business) return { success: false, error: "Negocio no encontrado" }

  // Validate every product belongs to this business and is active.
  // Never trust the client's product_id list: a forged id from another
  // business's catalogue would otherwise sneak through.
  const productIds = Array.from(new Set(input.items.map((i) => i.productId)))
  const products = await prisma.products.findMany({
    where: {
      id: { in: productIds },
      business_id: input.businessId,
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

  // Normalise the customer side. Three possible shapes:
  //   - existingCustomerId  → use as-is, ensure join row exists
  //   - whatsappId + name   → upsert global customer + join row (manual)
  //   - null                → anonymous order, customer_id and whatsapp_id stay null
  let resolvedCustomerId: number | null = null
  let resolvedWhatsappId: string | null = null

  if (input.customer && "existingCustomerId" in input.customer) {
    const existing = await prisma.customers.findUnique({
      where: { id: input.customer.existingCustomerId },
      select: { id: true, whatsapp_id: true },
    })
    if (!existing) {
      return { success: false, error: "Cliente no encontrado" }
    }
    resolvedCustomerId = existing.id
    resolvedWhatsappId = existing.whatsapp_id
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

  // Compute totals server-side. Decimal arithmetic via toFixed/parse keeps
  // us off floating-point banana-peel territory for COP totals.
  const subtotal = input.items.reduce(
    (acc, i) => acc + i.quantity * i.unitPrice,
    0
  )
  const totalAmount = Number((subtotal + deliveryFee).toFixed(2))

  try {
    const order = await prisma.$transaction(async (tx) => {
      // Make sure the customer (if any) is linked to this business so
      // they show up in the customers list. source='auto' since this
      // mirrors the bot path; manual creation already happened above
      // when a new customer was typed in.
      if (resolvedCustomerId !== null) {
        await tx.business_customers.upsert({
          where: {
            business_id_customer_id: {
              business_id: input.businessId,
              customer_id: resolvedCustomerId,
            },
          },
          create: {
            business_id: input.businessId,
            customer_id: resolvedCustomerId,
            source: "auto",
          },
          update: {},
        })
      }

      // Atomic per-business+day display number. The UPSERT row-lock
      // serializes concurrent inserts so two orders can't grab the same
      // number; the unique constraint on
      // (business_id, display_date, display_number) is the safety net.
      // Bogotá is UTC-5 year-round (no DST), so the timezone arithmetic
      // is stable.
      const counterRows = await tx.$queryRaw<
        { display_date: Date; last_value: number }[]
      >(Prisma.sql`
        INSERT INTO order_counters (business_id, display_date, last_value)
        VALUES (
          ${input.businessId}::uuid,
          (now() AT TIME ZONE 'America/Bogota')::date,
          1
        )
        ON CONFLICT (business_id, display_date)
        DO UPDATE SET last_value = order_counters.last_value + 1
        RETURNING display_date, last_value
      `)
      const counter = counterRows[0]
      if (!counter) {
        throw new Error("counter_allocation_failed")
      }

      const created = await tx.orders.create({
        data: {
          business_id: input.businessId,
          customer_id: resolvedCustomerId,
          whatsapp_id: resolvedWhatsappId,
          status: "pending",
          display_number: counter.last_value,
          display_date: counter.display_date,
          total_amount: new Prisma.Decimal(totalAmount.toFixed(2)),
          notes: input.notes?.trim() || null,
          fulfillment_type: fulfillmentType,
          delivery_address: isPickup ? null : input.deliveryAddress?.trim() || null,
          contact_phone: input.contactPhone?.trim() || null,
          payment_method: isPickup ? null : input.paymentMethod?.trim() || null,
          created_via: "admin",
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
        select: { id: true },
      })

      return created
    })

    revalidatePath(`/businesses/${input.businessId}/orders`)
    return { success: true, orderId: order.id }
  } catch (err) {
    return {
      success: false,
      error:
        err instanceof Error ? err.message : "No se pudo crear el pedido",
    }
  }
}
