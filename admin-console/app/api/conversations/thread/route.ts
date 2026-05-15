import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { canAccessConversations } from "@/lib/conversations-permissions";
import { getConversationThread } from "@/lib/conversations-queries";

const MAX_LIMIT = 100;

/**
 * Cursor-paginated thread fetch. The SSE stream serves the live latest
 * window; this endpoint serves on-demand older pages when the user
 * scrolls up. Same response shape either way so the client can route
 * the slice into its local message list without branching.
 */
export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const params = request.nextUrl.searchParams;
  const whatsappId = params.get("whatsappId");
  const businessId = params.get("businessId");
  const beforeRaw = params.get("before");
  const limitRaw = params.get("limit");

  if (!whatsappId || !businessId) {
    return NextResponse.json(
      { error: "whatsappId and businessId are required" },
      { status: 400 },
    );
  }
  if (!canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  const before = beforeRaw ? Number(beforeRaw) : undefined;
  if (before !== undefined && (!Number.isFinite(before) || before <= 0)) {
    return NextResponse.json(
      { error: "Invalid 'before' cursor" },
      { status: 400 },
    );
  }
  const limit = Math.min(
    Math.max(parseInt(limitRaw ?? "50", 10) || 50, 1),
    MAX_LIMIT,
  );

  try {
    const thread = await getConversationThread({
      whatsappId,
      businessId,
      limit,
      before,
    });
    return NextResponse.json(thread);
  } catch (err) {
    console.error("Error fetching conversation thread:", err);
    return NextResponse.json(
      { error: "Failed to fetch thread" },
      { status: 500 },
    );
  }
}
