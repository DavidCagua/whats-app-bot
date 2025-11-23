import { NextRequest, NextResponse } from "next/server"
import { exchangeCodeForTokens, saveGoogleCalendarCredentials } from "@/lib/actions/calendar"

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams
  const code = searchParams.get("code")
  const state = searchParams.get("state") // businessId
  const error = searchParams.get("error")

  // Get the base URL for redirect
  const baseUrl = process.env.NEXTAUTH_URL || request.nextUrl.origin

  if (error) {
    console.error("Google OAuth error:", error)
    return NextResponse.redirect(
      `${baseUrl}/businesses/${state}/settings?calendar_error=${encodeURIComponent(error)}`
    )
  }

  if (!code || !state) {
    return NextResponse.redirect(
      `${baseUrl}/businesses/${state || ""}/settings?calendar_error=missing_params`
    )
  }

  try {
    // Build the redirect URI (must match what was used in the auth request)
    const redirectUri = `${baseUrl}/api/calendar/callback`

    // Exchange the authorization code for tokens
    const tokens = await exchangeCodeForTokens(code, redirectUri)

    if (!tokens.refresh_token) {
      return NextResponse.redirect(
        `${baseUrl}/businesses/${state}/settings?calendar_error=no_refresh_token`
      )
    }

    // Save the credentials to the database
    const result = await saveGoogleCalendarCredentials(state, tokens)

    if (!result.success) {
      return NextResponse.redirect(
        `${baseUrl}/businesses/${state}/settings?calendar_error=${encodeURIComponent(result.error || "save_failed")}`
      )
    }

    // Success - redirect back to settings with success message
    return NextResponse.redirect(
      `${baseUrl}/businesses/${state}/settings?calendar_connected=true`
    )
  } catch (err) {
    console.error("Error in calendar callback:", err)
    const errorMessage = err instanceof Error ? err.message : "unknown_error"
    return NextResponse.redirect(
      `${baseUrl}/businesses/${state}/settings?calendar_error=${encodeURIComponent(errorMessage)}`
    )
  }
}
