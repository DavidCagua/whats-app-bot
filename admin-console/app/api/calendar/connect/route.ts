import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { getGoogleOAuthUrl } from "@/lib/actions/calendar"

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams
  const businessId = searchParams.get("businessId")

  if (!businessId) {
    return NextResponse.json({ error: "Business ID required" }, { status: 400 })
  }

  // Check authentication
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  // Check permissions
  if (!canEditBusiness(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 })
  }

  try {
    // Build the redirect URI
    const baseUrl = process.env.NEXTAUTH_URL || request.nextUrl.origin
    const redirectUri = `${baseUrl}/api/calendar/callback`

    // Get the Google OAuth URL
    const authUrl = await getGoogleOAuthUrl(businessId, redirectUri)

    // Redirect to Google OAuth
    return NextResponse.redirect(authUrl)
  } catch (err) {
    console.error("Error initiating calendar connection:", err)
    const errorMessage = err instanceof Error ? err.message : "Unknown error"
    return NextResponse.json({ error: errorMessage }, { status: 500 })
  }
}
