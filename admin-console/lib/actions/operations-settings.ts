"use server"

import type { Prisma } from "@prisma/client"
import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions"
import { revalidatePath } from "next/cache"
import { ETA_DROPDOWN_OPTIONS } from "@/lib/format-eta"

/**
 * Operator-set delivery controls that live in businesses.settings.
 * Both fields auto-expire at the end of the current business day (Bogotá)
 * so a forgotten override doesn't bleed into tomorrow's promises.
 */
export type OperationsSettings = {
  delivery_paused: boolean
  /** Lower-bound minutes (70 / 80 / 90). null = no override, use nominal 40-50. */
  delivery_eta_minutes: number | null
  /** ISO timestamp when the current override(s) expire. Read-only for the UI. */
  delivery_paused_until: string | null
  delivery_eta_until: string | null
}

type SettingsJsonShape = {
  delivery_paused?: boolean
  delivery_paused_until?: string | null
  delivery_eta_minutes?: number | null
  delivery_eta_until?: string | null
} & Record<string, unknown>

// Bogotá is fixed UTC-5 (no DST). Hard-coding the offset keeps timezone
// arithmetic simple — JS Date math in named zones requires Intl gymnastics
// and tends to drift across runtimes.
const BOGOTA_UTC_OFFSET_HOURS = -5

function bogotaWallParts(now: Date = new Date()): {
  year: number
  month: number
  day: number
  dayOfWeek: number
} {
  const shifted = new Date(
    now.getTime() + BOGOTA_UTC_OFFSET_HOURS * 60 * 60 * 1000,
  )
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth(),
    day: shifted.getUTCDate(),
    dayOfWeek: shifted.getUTCDay(),
  }
}

function bogotaWallToUtcInstant(
  year: number,
  month: number,
  day: number,
  hours: number,
  minutes: number,
): Date {
  return new Date(
    Date.UTC(year, month, day, hours - BOGOTA_UTC_OFFSET_HOURS, minutes, 0, 0),
  )
}

/**
 * Compute the auto-expiry boundary for operator overrides — end of
 * today's open window in Bogotá time. Falls back to tomorrow 04:00
 * Bogotá when no schedule row exists or today's close already passed,
 * which is safely after any restaurant close.
 */
async function computeValidUntil(businessId: string): Promise<Date> {
  const now = new Date()
  const { year, month, day, dayOfWeek } = bogotaWallParts(now)

  const todayRule = await prisma.business_availability.findFirst({
    where: { business_id: businessId, day_of_week: dayOfWeek, is_active: true },
  })

  if (todayRule?.close_time) {
    const close = new Date(todayRule.close_time)
    const closeUtc = bogotaWallToUtcInstant(
      year, month, day,
      close.getUTCHours(),
      close.getUTCMinutes(),
    )
    if (closeUtc > now) {
      return closeUtc
    }
  }

  // Fallback: tomorrow 04:00 Bogotá local.
  const tomorrowUtc = bogotaWallToUtcInstant(year, month, day + 1, 4, 0)
  return tomorrowUtc
}

export async function getOperationsSettings(
  businessId: string,
): Promise<OperationsSettings | null> {
  try {
    const session = await auth()
    if (!session?.user) return null
    if (!canAccessBusiness(session, businessId)) return null

    const business = await prisma.businesses.findUnique({
      where: { id: businessId },
    })
    if (!business) return null

    const s = (business.settings as SettingsJsonShape) || {}
    const now = Date.now()
    const pausedUntil = s.delivery_paused_until
      ? new Date(s.delivery_paused_until)
      : null
    const etaUntil = s.delivery_eta_until ? new Date(s.delivery_eta_until) : null
    const pausedActive =
      Boolean(s.delivery_paused) && (!pausedUntil || pausedUntil.getTime() > now)
    const etaActive =
      typeof s.delivery_eta_minutes === "number" &&
      s.delivery_eta_minutes > 0 &&
      (!etaUntil || etaUntil.getTime() > now)

    return {
      delivery_paused: pausedActive,
      delivery_eta_minutes: etaActive ? (s.delivery_eta_minutes as number) : null,
      delivery_paused_until:
        pausedActive && pausedUntil ? pausedUntil.toISOString() : null,
      delivery_eta_until: etaActive && etaUntil ? etaUntil.toISOString() : null,
    }
  } catch (error) {
    console.error("Error fetching operations settings:", error)
    return null
  }
}

export async function updateOperationsSettings(
  businessId: string,
  patch: { delivery_paused?: boolean; delivery_eta_minutes?: number | null },
): Promise<{ success: boolean; error?: string }> {
  try {
    const session = await auth()
    if (!session?.user) {
      return { success: false, error: "Unauthorized" }
    }
    if (!canEditBusiness(session, businessId)) {
      return {
        success: false,
        error: "You don't have permission to edit this business",
      }
    }

    if (patch.delivery_eta_minutes !== undefined && patch.delivery_eta_minutes !== null) {
      if (!ETA_DROPDOWN_OPTIONS.includes(patch.delivery_eta_minutes as 70 | 80 | 90)) {
        return { success: false, error: "Invalid delivery ETA minutes" }
      }
    }

    const current = await prisma.businesses.findUnique({
      where: { id: businessId },
    })
    if (!current) {
      return { success: false, error: "Business not found" }
    }

    const validUntil = await computeValidUntil(businessId)
    const existing = (current.settings as SettingsJsonShape) || {}
    const next: SettingsJsonShape = { ...existing }

    if (patch.delivery_paused !== undefined) {
      next.delivery_paused = patch.delivery_paused
      next.delivery_paused_until = patch.delivery_paused
        ? validUntil.toISOString()
        : null
    }

    if (patch.delivery_eta_minutes !== undefined) {
      if (patch.delivery_eta_minutes === null) {
        next.delivery_eta_minutes = null
        next.delivery_eta_until = null
      } else {
        next.delivery_eta_minutes = patch.delivery_eta_minutes
        next.delivery_eta_until = validUntil.toISOString()
      }
    }

    await prisma.businesses.update({
      where: { id: businessId },
      data: {
        settings: next as Prisma.InputJsonValue,
        updated_at: new Date(),
      },
    })

    revalidatePath(`/businesses/${businessId}/orders`)
    return { success: true }
  } catch (error) {
    console.error("Error updating operations settings:", error)
    return { success: false, error: "Failed to update operations settings" }
  }
}
