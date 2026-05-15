import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { canAccessConversations } from "@/lib/conversations-permissions";

export async function POST(request: NextRequest) {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await request.formData();
  const file = formData.get("file");
  const businessId = (formData.get("business_id") as string) ?? "";

  if (!file || !(file instanceof File)) {
    return NextResponse.json(
      { error: "Missing or invalid 'file' in multipart body" },
      { status: 400 },
    );
  }

  if (businessId && !canAccessConversations(session, businessId)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  const baseUrl = process.env.FLASK_API_BASE_URL;
  if (!baseUrl) {
    return NextResponse.json(
      { error: "FLASK_API_BASE_URL is not configured" },
      { status: 500 },
    );
  }

  const apiKey = process.env.ADMIN_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "ADMIN_API_KEY is not configured" },
      { status: 500 },
    );
  }

  const url = `${baseUrl.replace(/\/$/, "")}/admin/upload-media`;
  const forwardForm = new FormData();
  forwardForm.set("file", file);
  if (businessId) forwardForm.set("business_id", businessId);

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-Admin-API-Key": apiKey,
      },
      body: forwardForm,
    });

    const contentType = res.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await res.json()
      : await res.text();

    if (!res.ok) {
      return NextResponse.json(
        typeof payload === "object" ? payload : { error: payload },
        { status: res.status },
      );
    }

    return NextResponse.json(
      typeof payload === "object" ? payload : { url: payload },
    );
  } catch (err) {
    console.error("Error calling Flask upload-media endpoint:", err);
    return NextResponse.json(
      { error: "Failed to reach upload service" },
      { status: 502 },
    );
  }
}
