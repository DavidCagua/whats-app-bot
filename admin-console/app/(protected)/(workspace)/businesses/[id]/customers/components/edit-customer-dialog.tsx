"use client"

import { useState, useTransition } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "sonner"
import { useRouter } from "next/navigation"
import { updateCustomer } from "@/lib/actions/customers"
import type { CustomerRow } from "@/lib/customers-queries"

export function EditCustomerDialog({
  businessId,
  customer,
  open,
  onOpenChange,
}: {
  businessId: string
  customer: CustomerRow | null
  open: boolean
  onOpenChange: (next: boolean) => void
}) {
  const router = useRouter()
  const [isPending, startTransition] = useTransition()
  const [whatsappId, setWhatsappId] = useState("")
  const [name, setName] = useState("")
  const [phone, setPhone] = useState("")
  const [address, setAddress] = useState("")
  const [paymentMethod, setPaymentMethod] = useState("")
  const [notes, setNotes] = useState("")

  // Reset local state when the dialog re-opens with a different customer.
  // useEffect would also work, but keying off the customer id during render
  // keeps the form in sync without a flash of stale values.
  const [lastSeenId, setLastSeenId] = useState<number | null>(null)
  if (customer && customer.id !== lastSeenId) {
    setLastSeenId(customer.id)
    setWhatsappId(customer.whatsapp_id)
    setName(customer.name)
    setPhone(customer.phone ?? "")
    setAddress(customer.address ?? "")
    setPaymentMethod(customer.payment_method ?? "")
    setNotes(customer.notes ?? "")
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!customer) return
    startTransition(async () => {
      const result = await updateCustomer({
        businessId,
        customerId: customer.id,
        whatsappId,
        name,
        phone,
        address,
        paymentMethod,
        notes,
      })
      if (!result.success) {
        toast.error(result.error)
        return
      }
      toast.success("Cliente actualizado")
      onOpenChange(false)
      router.refresh()
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar cliente</DialogTitle>
          <DialogDescription>
            Nombre, teléfono, dirección y notas son por negocio. El WhatsApp
            es la identidad global del cliente — al cambiarlo se renombra
            en todos los negocios donde aparece.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="whatsappId">
              WhatsApp <span className="text-destructive">*</span>
            </Label>
            <Input
              id="whatsappId"
              inputMode="tel"
              placeholder="+573001234567"
              value={whatsappId}
              onChange={(e) => setWhatsappId(e.target.value)}
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="name">
              Nombre <span className="text-destructive">*</span>
            </Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="phone">Teléfono</Label>
            <Input
              id="phone"
              inputMode="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="address">Dirección</Label>
            <Textarea
              id="address"
              rows={2}
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="paymentMethod">Método de pago</Label>
            <Input
              id="paymentMethod"
              placeholder="Nequi, efectivo, …"
              value={paymentMethod}
              onChange={(e) => setPaymentMethod(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="notes">Notas internas</Label>
            <Textarea
              id="notes"
              rows={2}
              placeholder="Cliente VIP, prefiere sin cebolla, …"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>

          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Guardando…" : "Guardar"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
