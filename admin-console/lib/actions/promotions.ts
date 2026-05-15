"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"

export type PricingMode = "fixed_price" | "discount_amount" | "discount_pct"

export type PromotionComponentInput = {
  product_id: string
  quantity: number
}

export type PromotionInput = {
  name: string
  description?: string | null
  is_active?: boolean
  // Exactly ONE of these must be set; the others must be null/undefined.
  fixed_price?: number | null
  discount_amount?: number | null
  discount_pct?: number | null
  // Schedule (all optional; null = no constraint on that dimension).
  days_of_week?: number[] | null // ISO 1=Mon..7=Sun
  start_time?: string | null // "HH:MM" (24h)
  end_time?: string | null
  starts_on?: string | null // "YYYY-MM-DD"
  ends_on?: string | null
  components: PromotionComponentInput[]
}

export type SerializedPromotion = {
  id: string
  business_id: string
  name: string
  description: string | null
  is_active: boolean
  fixed_price: number | null
  discount_amount: number | null
  discount_pct: number | null
  days_of_week: number[] | null
  start_time: string | null
  end_time: string | null
  starts_on: string | null
  ends_on: string | null
  components: {
    id: string
    product_id: string
    product_name: string | null
    quantity: number
  }[]
  created_at: string | null
  updated_at: string | null
}

function promotionsPath(businessId: string) {
  return `/businesses/${businessId}/promotions`
}

function pickPricingMode(data: PromotionInput): PricingMode | null {
  const set = [
    data.fixed_price != null ? "fixed_price" : null,
    data.discount_amount != null ? "discount_amount" : null,
    data.discount_pct != null ? "discount_pct" : null,
  ].filter(Boolean) as PricingMode[]
  return set.length === 1 ? set[0] : null
}

// Time/date helpers — Prisma's Time/Date columns expect Date objects.
// We store TIME-only values by anchoring to 1970-01-01 in UTC, which is
// what `prisma db pull` represents these columns as.
function parseTime(hhmm: string | null | undefined): Date | null {
  if (!hhmm) return null
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim())
  if (!m) return null
  const h = Number(m[1])
  const mm = Number(m[2])
  if (h < 0 || h > 23 || mm < 0 || mm > 59) return null
  return new Date(Date.UTC(1970, 0, 1, h, mm, 0))
}

function parseDate(yyyymmdd: string | null | undefined): Date | null {
  if (!yyyymmdd) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(yyyymmdd.trim())
  if (!m) return null
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
}

function formatTime(d: Date | null): string | null {
  if (!d) return null
  // Read in UTC to round-trip with how parseTime stored it.
  const h = d.getUTCHours().toString().padStart(2, "0")
  const m = d.getUTCMinutes().toString().padStart(2, "0")
  return `${h}:${m}`
}

function formatDate(d: Date | null): string | null {
  if (!d) return null
  const y = d.getUTCFullYear()
  const m = (d.getUTCMonth() + 1).toString().padStart(2, "0")
  const day = d.getUTCDate().toString().padStart(2, "0")
  return `${y}-${m}-${day}`
}

type PromoRow = Awaited<
  ReturnType<typeof prisma.promotions.findUniqueOrThrow>
> & {
  promotion_components: {
    id: string
    product_id: string
    quantity: number
    products: { name: string } | null
  }[]
}

function serialize(p: PromoRow): SerializedPromotion {
  return {
    id: p.id,
    business_id: p.business_id,
    name: p.name,
    description: p.description ?? null,
    is_active: p.is_active,
    fixed_price: p.fixed_price != null ? Number(p.fixed_price.toString()) : null,
    discount_amount:
      p.discount_amount != null ? Number(p.discount_amount.toString()) : null,
    discount_pct: p.discount_pct ?? null,
    days_of_week: p.days_of_week ?? null,
    start_time: formatTime(p.start_time),
    end_time: formatTime(p.end_time),
    starts_on: formatDate(p.starts_on),
    ends_on: formatDate(p.ends_on),
    components: (p.promotion_components ?? []).map((c) => ({
      id: c.id,
      product_id: c.product_id,
      product_name: c.products?.name ?? null,
      quantity: c.quantity,
    })),
    created_at: p.created_at?.toISOString() ?? null,
    updated_at: p.updated_at?.toISOString() ?? null,
  }
}

const promoInclude = {
  promotion_components: {
    include: { products: { select: { name: true } } },
  },
} as const

export async function listPromotions(
  businessId: string
): Promise<SerializedPromotion[]> {
  const session = await auth()
  if (!session?.user) return []
  if (!canEditBusiness(session, businessId)) return []

  const rows = await prisma.promotions.findMany({
    where: { business_id: businessId },
    include: promoInclude,
    orderBy: [{ is_active: "desc" }, { name: "asc" }],
  })
  return rows.map((r) => serialize(r as PromoRow))
}

function validateInput(data: PromotionInput) {
  const name = data.name?.trim()
  if (!name) return "El nombre es obligatorio."
  if (name.length > 120) return "El nombre es demasiado largo."

  const mode = pickPricingMode(data)
  if (!mode) {
    return "Elige UN solo modo de precio (precio fijo, descuento $ o descuento %)."
  }
  if (mode === "discount_pct") {
    const pct = data.discount_pct!
    if (!Number.isInteger(pct) || pct <= 0 || pct > 100) {
      return "El descuento porcentual debe estar entre 1 y 100."
    }
  }
  if (mode === "fixed_price" && data.fixed_price! < 0) {
    return "El precio fijo no puede ser negativo."
  }
  if (mode === "discount_amount" && data.discount_amount! <= 0) {
    return "El descuento debe ser mayor a cero."
  }

  if (data.days_of_week) {
    for (const d of data.days_of_week) {
      if (!Number.isInteger(d) || d < 1 || d > 7) {
        return "Días inválidos (usa 1=lunes a 7=domingo)."
      }
    }
  }
  if (data.start_time && !parseTime(data.start_time)) {
    return "Hora de inicio inválida (formato HH:MM)."
  }
  if (data.end_time && !parseTime(data.end_time)) {
    return "Hora de fin inválida (formato HH:MM)."
  }
  if (data.starts_on && !parseDate(data.starts_on)) {
    return "Fecha de inicio inválida (YYYY-MM-DD)."
  }
  if (data.ends_on && !parseDate(data.ends_on)) {
    return "Fecha de fin inválida (YYYY-MM-DD)."
  }

  if (!Array.isArray(data.components) || data.components.length === 0) {
    return "Agrega al menos un producto a la promo."
  }
  for (const c of data.components) {
    if (!c.product_id) return "Cada componente necesita un producto."
    if (!Number.isInteger(c.quantity) || c.quantity < 1) {
      return "La cantidad debe ser un entero positivo."
    }
  }

  return null
}

export async function createPromotion(
  businessId: string,
  data: PromotionInput
) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }
  if (!canEditBusiness(session, businessId)) {
    return { success: false as const, error: "Forbidden" }
  }

  const validationError = validateInput(data)
  if (validationError) return { success: false as const, error: validationError }

  try {
    const created = await prisma.$transaction(async (tx) => {
      const promo = await tx.promotions.create({
        data: {
          business_id: businessId,
          name: data.name.trim(),
          description: data.description?.trim() || null,
          is_active: data.is_active ?? true,
          fixed_price: data.fixed_price ?? null,
          discount_amount: data.discount_amount ?? null,
          discount_pct: data.discount_pct ?? null,
          days_of_week: data.days_of_week && data.days_of_week.length > 0
            ? data.days_of_week
            : [],
          start_time: parseTime(data.start_time),
          end_time: parseTime(data.end_time),
          starts_on: parseDate(data.starts_on),
          ends_on: parseDate(data.ends_on),
          promotion_components: {
            create: data.components.map((c) => ({
              product_id: c.product_id,
              quantity: c.quantity,
            })),
          },
        },
        include: promoInclude,
      })
      return promo
    })
    revalidatePath(promotionsPath(businessId))
    return { success: true as const, promotion: serialize(created as PromoRow) }
  } catch (err) {
    console.error("createPromotion error:", err)
    return { success: false as const, error: "No se pudo crear la promoción." }
  }
}

export async function updatePromotion(
  promotionId: string,
  data: PromotionInput
) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.promotions.findUnique({
    where: { id: promotionId },
  })
  if (!existing) return { success: false as const, error: "Promo no encontrada." }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  const validationError = validateInput(data)
  if (validationError) return { success: false as const, error: validationError }

  try {
    const updated = await prisma.$transaction(async (tx) => {
      // Replace components wholesale — small set, simpler than diffing.
      await tx.promotion_components.deleteMany({
        where: { promotion_id: promotionId },
      })
      const promo = await tx.promotions.update({
        where: { id: promotionId },
        data: {
          name: data.name.trim(),
          description: data.description?.trim() || null,
          is_active: data.is_active ?? existing.is_active,
          // Clear non-selected pricing modes so the CHECK constraint
          // stays satisfied if the user switched modes.
          fixed_price: data.fixed_price ?? null,
          discount_amount: data.discount_amount ?? null,
          discount_pct: data.discount_pct ?? null,
          days_of_week: data.days_of_week && data.days_of_week.length > 0
            ? data.days_of_week
            : [],
          start_time: parseTime(data.start_time),
          end_time: parseTime(data.end_time),
          starts_on: parseDate(data.starts_on),
          ends_on: parseDate(data.ends_on),
          updated_at: new Date(),
          promotion_components: {
            create: data.components.map((c) => ({
              product_id: c.product_id,
              quantity: c.quantity,
            })),
          },
        },
        include: promoInclude,
      })
      return promo
    })
    revalidatePath(promotionsPath(existing.business_id))
    return { success: true as const, promotion: serialize(updated as PromoRow) }
  } catch (err) {
    console.error("updatePromotion error:", err)
    return { success: false as const, error: "No se pudo actualizar la promoción." }
  }
}

export async function setPromotionActive(promotionId: string, isActive: boolean) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.promotions.findUnique({
    where: { id: promotionId },
  })
  if (!existing) return { success: false as const, error: "Promo no encontrada." }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    const promo = await prisma.promotions.update({
      where: { id: promotionId },
      data: { is_active: isActive, updated_at: new Date() },
      include: promoInclude,
    })
    revalidatePath(promotionsPath(existing.business_id))
    return { success: true as const, promotion: serialize(promo as PromoRow) }
  } catch (err) {
    console.error("setPromotionActive error:", err)
    return { success: false as const, error: "No se pudo actualizar el estado." }
  }
}

export async function deletePromotion(promotionId: string) {
  const session = await auth()
  if (!session?.user) return { success: false as const, error: "Unauthorized" }

  const existing = await prisma.promotions.findUnique({
    where: { id: promotionId },
  })
  if (!existing) return { success: false as const, error: "Promo no encontrada." }
  if (!canEditBusiness(session, existing.business_id)) {
    return { success: false as const, error: "Forbidden" }
  }

  try {
    await prisma.promotions.delete({ where: { id: promotionId } })
    revalidatePath(promotionsPath(existing.business_id))
    return { success: true as const }
  } catch (err) {
    // Most likely cause: the promotion is referenced by an order_promotions
    // row (FK is RESTRICT). Surface a friendly message instead of crashing.
    console.error("deletePromotion error:", err)
    return {
      success: false as const,
      error: "No se puede borrar: la promo ya fue aplicada en pedidos. Desactívala mejor.",
    }
  }
}
