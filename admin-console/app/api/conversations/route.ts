import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getConversationsAccess } from "@/lib/conversations-permissions"
import { getConversations } from "@/lib/conversations-queries"

export async function GET(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const access = await getConversationsAccess(session)
  if (access.businessIds !== "all" && access.businessIds.length === 0) {
    return NextResponse.json({ error: "No business access" }, { status: 403 })
  }

  const searchParams = request.nextUrl.searchParams
  const businessFilter = searchParams.get("business") || undefined
  const searchQuery = searchParams.get("search") || undefined
  const dateFromParam = searchParams.get("dateFrom")
  const dateToParam = searchParams.get("dateTo")
  const limit = Math.min(parseInt(searchParams.get("limit") || "50", 10), 100)
  const offset = parseInt(searchParams.get("offset") || "0", 10)

  const dateFrom = dateFromParam ? new Date(dateFromParam) : undefined
  const dateTo = dateToParam ? new Date(dateToParam) : undefined

  try {
    const conversations = await getConversations({
      businessIds: access.businessIds,
      businessFilter,
      searchQuery,
      dateFrom,
      dateTo,
      limit,
      offset,
    })
    return NextResponse.json(conversations)
  } catch (err) {
    console.error("Error fetching conversations list:", err)
    return NextResponse.json(
      { error: "Failed to fetch conversations" },
      { status: 500 }
    )
  }
}
