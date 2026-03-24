import type { Session } from "next-auth"
import { getAccessibleBusinessIds, isSuperAdmin } from "@/lib/permissions"

/** Where to send the user immediately after login or when visiting `/`. */
export function getPostLoginRedirectPath(session: Session | null): string {
  if (!session?.user) return "/login"
  if (isSuperAdmin(session)) return "/businesses"
  const ids = getAccessibleBusinessIds(session)
  if (ids.length === 1) return `/businesses/${ids[0]}`
  return "/businesses"
}
