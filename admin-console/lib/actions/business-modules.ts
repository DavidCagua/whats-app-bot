"use server"

import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { prisma } from "@/lib/prisma"
import { revalidatePath } from "next/cache"
import { OPTIONAL_MODULES, type ModuleKey } from "@/lib/modules"

const ALLOWED_KEYS: Set<string> = new Set(OPTIONAL_MODULES.map((m) => m.key))

/**
 * Replace a business's enabled_modules with the given set. Restricted to
 * super admins. Required modules are not stored — silently filtered out
 * if included in the input. Unknown keys are rejected.
 */
export async function updateEnabledModules(
  businessId: string,
  modules: ModuleKey[]
): Promise<{ success: true } | { success: false; error: string }> {
  const session = await auth()
  if (!session?.user) return { success: false, error: "Unauthorized" }
  if (!isSuperAdmin(session)) {
    return { success: false, error: "Solo super admin puede modificar módulos" }
  }

  const sanitized = Array.from(
    new Set(modules.filter((m) => ALLOWED_KEYS.has(m)))
  )

  try {
    await prisma.businesses.update({
      where: { id: businessId },
      data: { enabled_modules: sanitized },
    })
    revalidatePath(`/businesses/${businessId}`, "layout")
    return { success: true }
  } catch (err) {
    console.error("updateEnabledModules error:", err)
    return { success: false, error: "No se pudo actualizar los módulos" }
  }
}
