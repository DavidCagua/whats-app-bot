"use client"

import { useState } from "react"
import { Booking } from "@/lib/bookings-queries"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Loader2, Trash2 } from "lucide-react"

const STATUS_OPTIONS = [
  { value: "confirmed", label: "Confirmed" },
  { value: "pending", label: "Pending" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "no_show", label: "No Show" },
]

function toDatetimeLocal(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

interface BookingModalProps {
  mode: "create" | "edit"
  booking?: Booking
  initialDate?: Date
  businesses: Array<{ id: string; name: string }>
  defaultBusinessId: string
  onClose: () => void
  onSaved: (booking: Booking) => void
  onDeleted: (id: string) => void
}

export function BookingModal({
  mode,
  booking,
  initialDate,
  businesses,
  defaultBusinessId,
  onClose,
  onSaved,
  onDeleted,
}: BookingModalProps) {
  const defaultStart = initialDate || (booking ? new Date(booking.start_at) : new Date())
  const defaultEnd = booking
    ? new Date(booking.end_at)
    : new Date(defaultStart.getTime() + 60 * 60 * 1000)

  const [businessId, setBusinessId] = useState(
    booking?.business_id || defaultBusinessId
  )
  const [serviceName, setServiceName] = useState(booking?.service_name || "")
  const [customerWhatsappId, setCustomerWhatsappId] = useState(
    booking?.customer?.whatsapp_id || ""
  )
  const [customerName, setCustomerName] = useState(
    booking?.customer?.name || ""
  )
  const [startAt, setStartAt] = useState(toDatetimeLocal(defaultStart))
  const [endAt, setEndAt] = useState(toDatetimeLocal(defaultEnd))
  const [status, setStatus] = useState(booking?.status || "confirmed")
  const [notes, setNotes] = useState(booking?.notes || "")
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        business_id: businessId,
        service_name: serviceName || null,
        start_at: new Date(startAt).toISOString(),
        end_at: new Date(endAt).toISOString(),
        status,
        notes: notes || null,
        customer_whatsapp_id: customerWhatsappId || undefined,
        customer_name: customerName || undefined,
      }

      const url = mode === "create" ? "/api/bookings" : `/api/bookings/${booking!.id}`
      const method = mode === "create" ? "POST" : "PATCH"

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || "Failed to save booking")
      }

      const saved = await res.json()
      // Normalize dates
      onSaved({
        ...saved,
        start_at: new Date(saved.start_at),
        end_at: new Date(saved.end_at),
        created_at: saved.created_at ? new Date(saved.created_at) : null,
        customer: saved.customers || saved.customer || null,
        business: saved.businesses || saved.business || { name: "" },
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!booking) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    setDeleting(true)
    setError(null)
    try {
      const res = await fetch(`/api/bookings/${booking.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cancelled" }),
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || "Failed to cancel booking")
      }
      onDeleted(booking.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error")
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {mode === "create" ? "New Booking" : "Edit Booking"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Business selector */}
          {businesses.length > 1 && (
            <div className="space-y-1">
              <Label>Business</Label>
              <Select value={businessId} onValueChange={setBusinessId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select business" />
                </SelectTrigger>
                <SelectContent>
                  {businesses.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Service */}
          <div className="space-y-1">
            <Label>Service</Label>
            <Input
              placeholder="e.g. Haircut, Consultation..."
              value={serviceName}
              onChange={(e) => setServiceName(e.target.value)}
            />
          </div>

          <Separator />

          {/* Customer */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Customer WhatsApp</Label>
              <Input
                placeholder="+57 300 000 0000"
                value={customerWhatsappId}
                onChange={(e) => setCustomerWhatsappId(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Customer Name</Label>
              <Input
                placeholder="Full name"
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
              />
            </div>
          </div>

          <Separator />

          {/* Date & time */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Start</Label>
              <Input
                type="datetime-local"
                value={startAt}
                onChange={(e) => setStartAt(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>End</Label>
              <Input
                type="datetime-local"
                value={endAt}
                onChange={(e) => setEndAt(e.target.value)}
              />
            </div>
          </div>

          {/* Status */}
          <div className="space-y-1">
            <Label>Status</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Notes */}
          <div className="space-y-1">
            <Label>Notes</Label>
            <Input
              placeholder="Optional notes..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter className="flex items-center gap-2">
          {mode === "edit" && (
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDelete}
              disabled={deleting || saving}
              className="mr-auto"
            >
              {deleting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4 mr-1" />
              )}
              {confirmDelete ? "Confirm Cancel" : "Cancel Booking"}
            </Button>
          )}

          <Button variant="outline" onClick={onClose} disabled={saving || deleting}>
            Dismiss
          </Button>
          <Button onClick={handleSave} disabled={saving || deleting}>
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Saving…
              </>
            ) : (
              "Save"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
