"use server"

import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canEditBusiness } from "@/lib/permissions"
import { encrypt } from "@/lib/encryption"
import { revalidatePath } from "next/cache"
import { Prisma } from "@prisma/client"

// Google Calendar OAuth configuration
const GOOGLE_CLIENT_ID = process.env.GOOGLE_OAUTH_CLIENT_ID
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_OAUTH_CLIENT_SECRET
const SCOPES = [
  "https://www.googleapis.com/auth/calendar",
  "https://www.googleapis.com/auth/userinfo.email",
]

export type GoogleCalendarSettings = {
  client_id: string
  client_secret: string // encrypted
  refresh_token: string // encrypted
  access_token?: string
  token_expiry?: string
  calendar_id: string
  is_configured: boolean
  connected_email?: string
}

export type CalendarStatus = {
  is_configured: boolean
  connected_email?: string
  calendar_id?: string
}

export async function getCalendarStatus(businessId: string): Promise<CalendarStatus> {
  const session = await auth()

  if (!session?.user) {
    throw new Error("Unauthorized")
  }

  if (!canEditBusiness(session, businessId)) {
    throw new Error("Access denied")
  }

  const business = await prisma.businesses.findUnique({
    where: { id: businessId },
    select: { settings: true },
  })

  if (!business) {
    throw new Error("Business not found")
  }

  const settings = business.settings as Record<string, unknown> | null
  const googleCalendar = settings?.google_calendar as GoogleCalendarSettings | undefined

  return {
    is_configured: googleCalendar?.is_configured ?? false,
    connected_email: googleCalendar?.connected_email,
    calendar_id: googleCalendar?.calendar_id,
  }
}

export async function disconnectGoogleCalendar(businessId: string) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!canEditBusiness(session, businessId)) {
    return { success: false, error: "Access denied" }
  }

  try {
    const business = await prisma.businesses.findUnique({
      where: { id: businessId },
      select: { settings: true },
    })

    if (!business) {
      return { success: false, error: "Business not found" }
    }

    const currentSettings = (business.settings as Record<string, unknown>) || {}

    // Remove google_calendar from settings
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { google_calendar: _, ...newSettings } = currentSettings

    await prisma.businesses.update({
      where: { id: businessId },
      data: {
        settings: newSettings as Prisma.InputJsonValue,
        updated_at: new Date(),
      },
    })

    revalidatePath(`/businesses/${businessId}/settings`)

    return { success: true }
  } catch (error) {
    console.error("Error disconnecting Google Calendar:", error)
    return { success: false, error: "Failed to disconnect calendar" }
  }
}

export async function saveGoogleCalendarCredentials(
  businessId: string,
  credentials: {
    refresh_token: string
    access_token: string
    expires_in: number
    connected_email?: string
  }
) {
  const session = await auth()

  if (!session?.user) {
    return { success: false, error: "Unauthorized" }
  }

  if (!canEditBusiness(session, businessId)) {
    return { success: false, error: "Access denied" }
  }

  if (!GOOGLE_CLIENT_ID || !GOOGLE_CLIENT_SECRET) {
    return { success: false, error: "Google OAuth not configured on server" }
  }

  try {
    const business = await prisma.businesses.findUnique({
      where: { id: businessId },
      select: { settings: true },
    })

    if (!business) {
      return { success: false, error: "Business not found" }
    }

    const currentSettings = (business.settings as Record<string, unknown>) || {}

    // Calculate token expiry
    const tokenExpiry = new Date(Date.now() + credentials.expires_in * 1000).toISOString()

    // Create encrypted calendar settings
    const googleCalendarSettings: GoogleCalendarSettings = {
      client_id: GOOGLE_CLIENT_ID,
      client_secret: encrypt(GOOGLE_CLIENT_SECRET),
      refresh_token: encrypt(credentials.refresh_token),
      access_token: credentials.access_token,
      token_expiry: tokenExpiry,
      calendar_id: "primary",
      is_configured: true,
      connected_email: credentials.connected_email,
    }

    const newSettings = {
      ...currentSettings,
      google_calendar: googleCalendarSettings,
    }

    await prisma.businesses.update({
      where: { id: businessId },
      data: {
        settings: newSettings as Prisma.InputJsonValue,
        updated_at: new Date(),
      },
    })

    revalidatePath(`/businesses/${businessId}/settings`)

    return { success: true }
  } catch (error) {
    console.error("Error saving Google Calendar credentials:", error)
    return { success: false, error: "Failed to save calendar credentials" }
  }
}

export async function getGoogleOAuthUrl(businessId: string, redirectUri: string): Promise<string> {
  if (!GOOGLE_CLIENT_ID) {
    throw new Error("Google OAuth not configured")
  }

  const params = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: SCOPES.join(" "),
    access_type: "offline",
    prompt: "consent",
    state: businessId, // Pass businessId as state to retrieve after callback
  })

  return `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`
}

export async function exchangeCodeForTokens(code: string, redirectUri: string) {
  if (!GOOGLE_CLIENT_ID || !GOOGLE_CLIENT_SECRET) {
    throw new Error("Google OAuth not configured")
  }

  const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      code,
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  })

  if (!tokenResponse.ok) {
    const error = await tokenResponse.text()
    throw new Error(`Token exchange failed: ${error}`)
  }

  const tokens = await tokenResponse.json()

  // Get user email from the access token
  let connectedEmail: string | undefined
  try {
    const userInfoResponse = await fetch(
      "https://www.googleapis.com/oauth2/v2/userinfo",
      {
        headers: {
          Authorization: `Bearer ${tokens.access_token}`,
        },
      }
    )
    if (userInfoResponse.ok) {
      const userInfo = await userInfoResponse.json()
      connectedEmail = userInfo.email
    }
  } catch {
    // Email fetch failed, continue without it
  }

  return {
    refresh_token: tokens.refresh_token,
    access_token: tokens.access_token,
    expires_in: tokens.expires_in,
    connected_email: connectedEmail,
  }
}
