import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { canAccessConversations } from "@/lib/conversations-permissions";
import { prisma } from "@/lib/prisma";
import { randomUUID } from "crypto";

export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const searchParams = request.nextUrl.searchParams;
  const whatsappId = searchParams.get("whatsappId");
  const businessId = searchParams.get("businessId");

  if (!whatsappId || !businessId) {
    return NextResponse.json(
      { error: "whatsappId and businessId are required" },
      { status: 400 },
    );
  }

  if (!canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  const row = await prisma.conversation_agent_settings.findFirst({
    where: { business_id: businessId, whatsapp_id: whatsappId },
    orderBy: { updated_at: "desc" },
    select: { agent_enabled: true, handoff_reason: true },
  });

  return NextResponse.json({
    agentEnabled: row?.agent_enabled ?? true,
    handoffReason: row?.handoff_reason ?? null,
  });
}

export async function PATCH(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = body as {
    whatsappId?: string;
    businessId?: string;
    agentEnabled?: boolean;
  };

  const whatsappId = parsed.whatsappId;
  const businessId = parsed.businessId;
  const agentEnabled = parsed.agentEnabled;

  if (!whatsappId || !businessId || typeof agentEnabled !== "boolean") {
    return NextResponse.json(
      { error: "whatsappId, businessId, and agentEnabled are required" },
      { status: 400 },
    );
  }

  if (!canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  // Manual upsert (avoid ON CONFLICT errors if DB constraint isn't applied yet)
  const existing = await prisma.conversation_agent_settings.findFirst({
    where: { business_id: businessId, whatsapp_id: whatsappId },
    orderBy: { updated_at: "desc" },
    select: { id: true },
  });

  // Re-enabling always clears the handoff reason so the colored
  // treatments in the conversation list / orders table go back to
  // normal once staff takes the conversation back.
  const handoffReasonOnWrite = agentEnabled ? null : undefined;
  if (existing?.id) {
    await prisma.conversation_agent_settings.update({
      where: { id: existing.id },
      data: {
        agent_enabled: agentEnabled,
        ...(handoffReasonOnWrite === null ? { handoff_reason: null } : {}),
        updated_at: new Date(),
      },
    });
  } else {
    await prisma.conversation_agent_settings.create({
      data: {
        id: randomUUID(),
        business_id: businessId,
        whatsapp_id: whatsappId,
        agent_enabled: agentEnabled,
        updated_at: new Date(),
      },
    });
  }

  return NextResponse.json({ ok: true });
}
