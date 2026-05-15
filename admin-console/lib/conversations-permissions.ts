import { Session } from "next-auth";
import { prisma } from "./prisma";
import { isSuperAdmin } from "./permissions";

export type ConversationsAccess = {
  businessIds: string[] | "all";
  canFilterByBusiness: boolean;
  canSeeAllStats: boolean;
  canExport: boolean;
  businesses: Array<{ id: string; name: string }>;
  whatsappNumbers: Array<{
    id: string;
    phone_number: string;
    business_id: string;
  }>;
};

/**
 * Get conversations access permissions for the current user
 * Determines what conversations they can see and what actions they can perform
 */
export async function getConversationsAccess(
  session: Session | null,
): Promise<ConversationsAccess> {
  if (!session?.user) {
    return {
      businessIds: [],
      canFilterByBusiness: false,
      canSeeAllStats: false,
      canExport: false,
      businesses: [],
      whatsappNumbers: [],
    };
  }

  // Super admins see everything
  if (isSuperAdmin(session)) {
    const [businesses, whatsappNumbers] = await Promise.all([
      prisma.businesses.findMany({
        where: { is_active: true },
        select: { id: true, name: true },
        orderBy: { name: "asc" },
      }),
      prisma.whatsapp_numbers.findMany({
        where: { is_active: true },
        select: { id: true, phone_number: true, business_id: true },
        orderBy: { phone_number: "asc" },
      }),
    ]);

    return {
      businessIds: "all",
      canFilterByBusiness: true,
      canSeeAllStats: true,
      canExport: true,
      businesses,
      whatsappNumbers,
    };
  }

  // Get user's business associations
  const userBusinesses = session.user.businesses || [];
  const businessIds = userBusinesses.map((b) => b.businessId);

  if (businessIds.length === 0) {
    return {
      businessIds: [],
      canFilterByBusiness: false,
      canSeeAllStats: false,
      canExport: false,
      businesses: [],
      whatsappNumbers: [],
    };
  }

  // Fetch businesses and WhatsApp numbers for user's businesses
  const [businesses, whatsappNumbers] = await Promise.all([
    prisma.businesses.findMany({
      where: {
        id: { in: businessIds },
        is_active: true,
      },
      select: { id: true, name: true },
      orderBy: { name: "asc" },
    }),
    prisma.whatsapp_numbers.findMany({
      where: {
        business_id: { in: businessIds },
        is_active: true,
      },
      select: { id: true, phone_number: true, business_id: true },
      orderBy: { phone_number: "asc" },
    }),
  ]);

  // Check if user is owner/admin of any business
  const isOwner = userBusinesses.some((b) => b.role === "admin");

  return {
    businessIds,
    canFilterByBusiness: businessIds.length > 1,
    canSeeAllStats: isOwner,
    canExport: isOwner,
    businesses,
    whatsappNumbers,
  };
}

/**
 * Check if user can access conversations for a specific business
 */
export function canAccessConversations(
  session: Session | null,
  businessId: string,
): boolean {
  if (!session?.user) return false;

  // Super admins can access all
  if (isSuperAdmin(session)) return true;

  // Check if user has access to this business
  return (
    session.user.businesses?.some((b) => b.businessId === businessId) || false
  );
}
