"use server";

import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions";
import { revalidatePath } from "next/cache";

const AGENT_TYPES = [
  {
    type: "booking",
    name: "Booking Agent",
    description: "Handles appointment scheduling and availability management",
  },
  {
    type: "order",
    name: "Order Agent",
    description: "Handles restaurant/retail orders",
  },
  {
    type: "sales",
    name: "Sales Agent",
    description: "Handles product sales and checkout",
  },
  {
    type: "support",
    name: "Support Agent",
    description: "Handles customer support and tickets",
  },
] as const;

export type AgentType = (typeof AGENT_TYPES)[number]["type"];

export type BusinessAgentConfig = {
  agent_type: AgentType;
  enabled: boolean;
  priority: number;
  name: string;
  description: string;
};

export async function getBusinessAgents(
  businessId: string,
): Promise<BusinessAgentConfig[] | null> {
  try {
    const session = await auth();
    if (!session?.user) {
      throw new Error("Unauthorized");
    }

    if (!canAccessBusiness(session, businessId)) {
      throw new Error("Access denied to this business");
    }

    const rows = await prisma.business_agents.findMany({
      where: { business_id: businessId },
      orderBy: { priority: "asc" },
    });

    return AGENT_TYPES.map((def) => {
      const row = rows.find((r) => r.agent_type === def.type);
      return {
        agent_type: def.type,
        enabled: row?.enabled ?? def.type === "booking",
        priority: row?.priority ?? (def.type === "booking" ? 1 : 100),
        name: def.name,
        description: def.description,
      };
    });
  } catch (error) {
    console.error("Error fetching business agents:", error);
    return null;
  }
}

export async function updateBusinessAgents(
  businessId: string,
  updates: { agent_type: AgentType; enabled: boolean; priority?: number }[],
) {
  try {
    const session = await auth();
    if (!session?.user) {
      return { success: false, error: "Unauthorized" };
    }

    if (!canEditBusiness(session, businessId)) {
      return {
        success: false,
        error: "You don't have permission to edit this business",
      };
    }

    const enabledCount = updates.filter((u) => u.enabled).length;
    if (enabledCount === 0) {
      return { success: false, error: "At least one agent must be enabled" };
    }

    for (const u of updates) {
      await prisma.business_agents.upsert({
        where: {
          business_id_agent_type: {
            business_id: businessId,
            agent_type: u.agent_type,
          },
        },
        create: {
          business_id: businessId,
          agent_type: u.agent_type,
          enabled: u.enabled,
          priority: u.priority ?? 100,
          config: {},
        },
        update: {
          enabled: u.enabled,
          ...(u.priority !== undefined && { priority: u.priority }),
          updated_at: new Date(),
        },
      });
    }

    revalidatePath(`/businesses/${businessId}/settings`);

    return { success: true };
  } catch (error) {
    console.error("Error updating business agents:", error);
    return { success: false, error: "Failed to update agents" };
  }
}
