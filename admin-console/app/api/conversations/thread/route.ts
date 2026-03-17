import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { canAccessConversations } from "@/lib/conversations-permissions"
import { getConversationThread } from "@/lib/conversations-queries"

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const searchParams = request.nextUrl.searchParams
  const whatsappId = searchParams.get("whatsappId")
  const businessId = searchParams.get("businessId")

  if (!whatsappId || !businessId) {
    return NextResponse.json(
      { error: "whatsappId and businessId are required" },
      { status: 400 }
    )
  }

  if (!canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 })
  }

  try {
    const thread = await getConversationThread({ whatsappId, businessId })
    return NextResponse.json(thread)
  } catch (err) {
    console.error("Error fetching conversation thread:", err)
    return NextResponse.json(
      { error: "Failed to fetch thread" },
      { status: 500 }
    )
  }
}
