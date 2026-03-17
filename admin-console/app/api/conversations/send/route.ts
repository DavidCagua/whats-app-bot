import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { canAccessConversations } from "@/lib/conversations-permissions"

type Body = {
  whatsappId?: string
  businessId?: string
  text?: string
  /** Meta phone_number_id (or twilio:...) for the channel; must match the number used for this conversation. */
  phoneNumberId?: string | null
  /** E.164 phone number for the channel when phone_number_id is null in DB; used for send lookup. */
  phoneNumber?: string | null
}

export async function POST(request: NextRequest) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  let body: Body
  try {
    body = await request.json()
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 })
  }

  const whatsappId = body.whatsappId
  const businessId = body.businessId
  const text = body.text
  const phoneNumberId = body.phoneNumberId
  const phoneNumber = body.phoneNumber

  if (!whatsappId || !businessId || !text || !text.trim()) {
    return NextResponse.json(
      { error: "whatsappId, businessId, and text are required" },
      { status: 400 }
    )
  }

  if (!canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 })
  }

  const baseUrl = process.env.FLASK_API_BASE_URL
  if (!baseUrl) {
    return NextResponse.json(
      { error: "FLASK_API_BASE_URL is not configured" },
      { status: 500 }
    )
  }

  const apiKey = process.env.ADMIN_API_KEY
  if (!apiKey) {
    return NextResponse.json(
      { error: "ADMIN_API_KEY is not configured" },
      { status: 500 }
    )
  }

  const url = `${baseUrl.replace(/\/$/, "")}/admin/send-message`

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-API-Key": apiKey,
      },
      body: JSON.stringify({
        whatsapp_id: whatsappId,
        business_id: businessId,
        text,
        ...(phoneNumberId ? { phone_number_id: phoneNumberId } : {}),
        ...(phoneNumber ? { phone_number: phoneNumber } : {}),
      }),
    })

    const contentType = res.headers.get("content-type") || ""
    const payload = contentType.includes("application/json")
      ? await res.json()
      : await res.text()

    if (!res.ok) {
      return NextResponse.json(
        {
          error: "Failed to send message",
          details: payload,
        },
        { status: res.status }
      )
    }

    return NextResponse.json({ ok: true, result: payload })
  } catch (err) {
    console.error("Error calling Flask send-message endpoint:", err)
    return NextResponse.json(
      { error: "Failed to reach messaging service" },
      { status: 502 }
    )
  }
}

