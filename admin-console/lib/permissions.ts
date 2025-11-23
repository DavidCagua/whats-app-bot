import { Session } from "next-auth"

/**
 * Permission helpers for role-based access control
 *
 * Role hierarchy:
 * - super_admin (users.role): OmnIA team - full system access to all businesses
 * - admin (user_businesses.role): Business owner/admin - can edit their business
 * - staff (user_businesses.role): Business employee - view-only access
 */

export function isSuperAdmin(session: Session | null): boolean {
  return session?.user?.role === "super_admin"
}

export function canAccessBusiness(
  session: Session | null,
  businessId: string
): boolean {
  if (!session?.user) return false

  // Super admins can access all businesses
  if (isSuperAdmin(session)) return true

  // Check if user has any association with this business
  return session.user.businesses?.some((b) => b.businessId === businessId)
}

export function canEditBusiness(
  session: Session | null,
  businessId: string
): boolean {
  if (!session?.user) return false

  // Super admins can edit all businesses
  if (isSuperAdmin(session)) return true

  // Check if user has admin role for this business
  const business = session.user.businesses?.find(
    (b) => b.businessId === businessId
  )
  return business?.role === "admin"
}

export function getUserBusinessRole(
  session: Session | null,
  businessId: string
): string | null {
  if (!session?.user) return null

  // Super admins effectively have admin access to everything
  if (isSuperAdmin(session)) return "super_admin"

  const business = session.user.businesses?.find(
    (b) => b.businessId === businessId
  )
  return business?.role || null
}

export function getAccessibleBusinessIds(session: Session | null): string[] {
  if (!session?.user) return []

  // Super admins have access to all (return empty to signal "all")
  // The calling code should handle this case
  if (isSuperAdmin(session)) return []

  return session.user.businesses?.map((b) => b.businessId) || []
}
