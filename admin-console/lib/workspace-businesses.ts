import type { Session } from "next-auth";
import { prisma } from "@/lib/prisma";
import { isSuperAdmin } from "@/lib/permissions";

export type SwitcherBusiness = { id: string; name: string };

/** Businesses the user can open in the workspace switcher (ordered by name). */
export async function getWorkspaceSwitcherBusinesses(
  session: Session | null,
): Promise<SwitcherBusiness[]> {
  if (!session?.user) return [];

  if (isSuperAdmin(session)) {
    const rows = await prisma.businesses.findMany({
      where: { is_active: true },
      select: { id: true, name: true },
      orderBy: { name: "asc" },
    });
    return rows;
  }

  const fromSession = session.user.businesses || [];
  return [...fromSession]
    .map((b) => ({ id: b.businessId, name: b.businessName }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
