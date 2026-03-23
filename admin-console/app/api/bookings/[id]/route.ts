import { NextRequest, NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getBookingsAccess } from "@/lib/bookings-queries"
import { prisma } from "@/lib/prisma"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const { id } = await params

  const booking = await prisma.bookings.findUnique({
    where: { id },
    include: {
      customers: { select: { name: true, whatsapp_id: true } },
      businesses: { select: { name: true } },
    },
  })

  if (!booking) {
    return NextResponse.json({ error: "Not found" }, { status: 404 })
  }

  const access = await getBookingsAccess(session)
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(booking.business_id)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 })
  }

  return NextResponse.json(booking)
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth()
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const { id } = await params

  const existing = await prisma.bookings.findUnique({ where: { id } })
  if (!existing) {
    return NextResponse.json({ error: "Not found" }, { status: 404 })
  }

  const access = await getBookingsAccess(session)
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(existing.business_id)
  ) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 })
  }

  try {
    const body = await request.json()
    const {
      service_name,
      start_at,
      end_at,
      status,
      notes,
      customer_whatsapp_id,
      customer_name,
    } = body

    // Resolve customer if provided
    let customer_id: number | null | undefined = undefined
    if (customer_whatsapp_id !== undefined) {
      if (customer_whatsapp_id === null || customer_whatsapp_id === "") {
        customer_id = null
      } else {
        let customer = await prisma.customers.findUnique({
          where: { whatsapp_id: customer_whatsapp_id },
        })
        if (!customer) {
          customer = await prisma.customers.create({
            data: {
              whatsapp_id: customer_whatsapp_id,
              name: customer_name || customer_whatsapp_id,
              created_at: new Date(),
              updated_at: new Date(),
            },
          })
        } else if (customer_name && customer.name !== customer_name) {
          customer = await prisma.customers.update({
            where: { whatsapp_id: customer_whatsapp_id },
            data: { name: customer_name, updated_at: new Date() },
          })
        }
        customer_id = customer.id
      }
    }

    const updateData: Record<string, unknown> = { updated_at: new Date() }
    if (service_name !== undefined) updateData.service_name = service_name
    if (start_at !== undefined) updateData.start_at = new Date(start_at)
    if (end_at !== undefined) updateData.end_at = new Date(end_at)
    if (status !== undefined) updateData.status = status
    if (notes !== undefined) updateData.notes = notes
    if (customer_id !== undefined) updateData.customer_id = customer_id

    const updated = await prisma.bookings.update({
      where: { id },
      data: updateData,
      include: {
        customers: { select: { name: true, whatsapp_id: true } },
        businesses: { select: { name: true } },
      },
    })

    return NextResponse.json(updated)
  } catch (err) {
    console.error("Error updating booking:", err)
    return NextResponse.json({ error: "Failed to update booking" }, { status: 500 })
  }
}
