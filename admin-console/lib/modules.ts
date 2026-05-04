import { redirect } from "next/navigation"

/**
 * Per-business module registry. Each entry corresponds to a sidebar
 * section + a route segment. `required: true` modules are always
 * available regardless of `business.enabled_modules`; the rest can be
 * toggled per-business by a super admin from the settings page.
 *
 * The registry is the single source of truth — sidebar rendering, route
 * guards, and the super-admin toggle UI all read from here.
 */
export type ModuleKey =
  | "overview"
  | "inbox"
  | "bookings"
  | "orders"
  | "products"
  | "promotions"
  | "services"
  | "staff"
  | "team"
  | "settings"

export type Module = {
  key: ModuleKey
  /** Display label in the sidebar / settings UI. */
  label: string
  /** Path segment appended to /businesses/[id]. Empty string for the overview page. */
  hrefSegment: string
  /** Required modules always render and cannot be disabled. */
  required: boolean
  /** Short description shown on the super-admin toggle card. */
  description?: string
}

export const MODULES: readonly Module[] = [
  { key: "overview", label: "Resumen", hrefSegment: "", required: true },
  {
    key: "inbox",
    label: "Bandeja de entrada",
    hrefSegment: "/inbox",
    required: true,
  },
  {
    key: "bookings",
    label: "Reservas",
    hrefSegment: "/bookings",
    required: false,
    description: "Citas y reservas con calendario.",
  },
  {
    key: "orders",
    label: "Pedidos",
    hrefSegment: "/orders",
    required: false,
    description: "Pedidos creados por el bot y gestión de estados.",
  },
  {
    key: "products",
    label: "Productos",
    hrefSegment: "/products",
    required: false,
    description: "Catálogo de productos del negocio.",
  },
  {
    key: "promotions",
    label: "Promociones",
    hrefSegment: "/promotions",
    required: false,
    description: "Promociones activas y campañas.",
  },
  {
    key: "services",
    label: "Servicios",
    hrefSegment: "/services",
    required: false,
    description: "Servicios ofrecidos (peluquería, spa, etc.).",
  },
  {
    key: "staff",
    label: "Personal",
    hrefSegment: "/staff",
    required: false,
    description: "Miembros del personal y asignaciones.",
  },
  {
    key: "team",
    label: "Acceso",
    hrefSegment: "/team",
    required: true,
  },
  {
    key: "settings",
    label: "Configuración",
    hrefSegment: "/settings",
    required: true,
  },
] as const

export const OPTIONAL_MODULES: readonly Module[] = MODULES.filter(
  (m) => !m.required
)

const MODULES_BY_KEY = new Map<ModuleKey, Module>(
  MODULES.map((m) => [m.key, m])
)

export function getModule(key: ModuleKey): Module | undefined {
  return MODULES_BY_KEY.get(key)
}

/**
 * Returns true if the given module is enabled for this business. Required
 * modules are always enabled; optional modules check membership in the
 * business's `enabled_modules` list.
 */
export function isModuleEnabled(
  business: { enabled_modules: string[] } | null | undefined,
  key: ModuleKey
): boolean {
  const mod = MODULES_BY_KEY.get(key)
  if (!mod) return false
  if (mod.required) return true
  if (!business) return false
  return business.enabled_modules.includes(key)
}

/**
 * Filter the registry down to the modules the given business should
 * surface in its sidebar.
 */
export function getEnabledModules(business: {
  enabled_modules: string[]
}): readonly Module[] {
  return MODULES.filter((m) => isModuleEnabled(business, m.key))
}

/**
 * Redirect to the business overview if the given module is disabled.
 * Use at the top of a gated page after `auth()` + `canAccessBusiness`.
 *
 *   await redirectIfModuleDisabled(businessId, "bookings")
 *
 * Reads the business row directly so the helper is self-contained;
 * pages already do their own auth check above this call.
 */
export async function redirectIfModuleDisabled(
  businessId: string,
  key: ModuleKey
): Promise<void> {
  const mod = MODULES_BY_KEY.get(key)
  if (!mod || mod.required) return
  // Lazy import to avoid pulling Prisma into client bundles that re-export
  // helpers from this module.
  const { prisma } = await import("@/lib/prisma")
  const business = await prisma.businesses.findUnique({
    where: { id: businessId },
    select: { enabled_modules: true },
  })
  if (!business || !isModuleEnabled(business, key)) {
    redirect(`/businesses/${businessId}`)
  }
}
