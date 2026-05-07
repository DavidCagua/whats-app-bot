"use client"

import { useMemo, useState, useTransition } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { useRouter } from "next/navigation"
import { createOrder } from "@/lib/actions/orders-create"
import type {
  CustomerOption,
  ProductOption,
} from "@/lib/orders-create-data"

const NEW_CUSTOMER = "__new__"
const NO_CUSTOMER = "__none__"

type ItemDraft = {
  productId: string
  quantity: number
  unitPrice: number
  notes: string
}

const formatCOP = (n: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(n)

const emptyItem = (): ItemDraft => ({
  productId: "",
  quantity: 1,
  unitPrice: 0,
  notes: "",
})

export function CreateOrderDialog({
  businessId,
  products,
  customers,
}: {
  businessId: string
  products: ProductOption[]
  customers: CustomerOption[]
}) {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [isPending, startTransition] = useTransition()

  const [customerChoice, setCustomerChoice] = useState<string>(NO_CUSTOMER)
  const [newWhatsappId, setNewWhatsappId] = useState("")
  const [newCustomerName, setNewCustomerName] = useState("")

  const [items, setItems] = useState<ItemDraft[]>([emptyItem()])
  const [deliveryAddress, setDeliveryAddress] = useState("")
  const [contactPhone, setContactPhone] = useState("")
  const [paymentMethod, setPaymentMethod] = useState("")
  const [deliveryFee, setDeliveryFee] = useState(0)
  const [notes, setNotes] = useState("")

  const productById = useMemo(
    () => new Map(products.map((p) => [p.id, p])),
    [products]
  )

  const subtotal = useMemo(
    () => items.reduce((acc, i) => acc + i.quantity * i.unitPrice, 0),
    [items]
  )
  const total = subtotal + (Number.isFinite(deliveryFee) ? deliveryFee : 0)

  function reset() {
    setCustomerChoice(NO_CUSTOMER)
    setNewWhatsappId("")
    setNewCustomerName("")
    setItems([emptyItem()])
    setDeliveryAddress("")
    setContactPhone("")
    setPaymentMethod("")
    setDeliveryFee(0)
    setNotes("")
  }

  function updateItem(idx: number, patch: Partial<ItemDraft>) {
    setItems((prev) =>
      prev.map((it, i) => (i === idx ? { ...it, ...patch } : it))
    )
  }

  function onSelectProduct(idx: number, productId: string) {
    const p = productById.get(productId)
    updateItem(idx, {
      productId,
      // Prefill the unit price from the catalogue, but keep it editable
      // so the admin can apply a manual discount (no promo engine in v1).
      unitPrice: p ? p.price : 0,
    })
  }

  const canSubmit =
    items.length > 0 &&
    items.every(
      (i) => i.productId && i.quantity > 0 && i.unitPrice >= 0
    ) &&
    (customerChoice !== NEW_CUSTOMER ||
      (newWhatsappId.trim() && newCustomerName.trim()))

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!canSubmit) return

    const customer =
      customerChoice === NO_CUSTOMER
        ? null
        : customerChoice === NEW_CUSTOMER
          ? { whatsappId: newWhatsappId.trim(), name: newCustomerName.trim() }
          : { existingCustomerId: Number(customerChoice) }

    startTransition(async () => {
      const result = await createOrder({
        businessId,
        customer,
        items: items.map((i) => ({
          productId: i.productId,
          quantity: i.quantity,
          unitPrice: i.unitPrice,
          notes: i.notes,
        })),
        deliveryAddress,
        contactPhone,
        paymentMethod,
        deliveryFee,
        notes,
      })
      if (!result.success) {
        toast.error(result.error)
        return
      }
      toast.success("Pedido creado")
      reset()
      setOpen(false)
      router.refresh()
    })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Plus className="h-4 w-4" />
          Crear pedido
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Nuevo pedido</DialogTitle>
          <DialogDescription>
            Agrega ítems y, si quieres, asocia un cliente. Sin promos
            automáticos — el precio que escribas es el final.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-4">
          {/* Customer */}
          <section className="space-y-2">
            <Label htmlFor="customer">Cliente</Label>
            <Select value={customerChoice} onValueChange={setCustomerChoice}>
              <SelectTrigger id="customer">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_CUSTOMER}>Sin cliente</SelectItem>
                <SelectItem value={NEW_CUSTOMER}>Cliente nuevo…</SelectItem>
                {customers.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name} · {c.whatsapp_id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {customerChoice === NEW_CUSTOMER && (
              <div className="grid grid-cols-2 gap-2 pt-1">
                <div className="space-y-1.5">
                  <Label htmlFor="newWa" className="text-xs">
                    WhatsApp
                  </Label>
                  <Input
                    id="newWa"
                    inputMode="tel"
                    placeholder="+573001234567"
                    value={newWhatsappId}
                    onChange={(e) => setNewWhatsappId(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="newName" className="text-xs">
                    Nombre
                  </Label>
                  <Input
                    id="newName"
                    value={newCustomerName}
                    onChange={(e) => setNewCustomerName(e.target.value)}
                  />
                </div>
              </div>
            )}
          </section>

          {/* Items */}
          <section className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Ítems</Label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setItems((prev) => [...prev, emptyItem()])}
                className="gap-1"
              >
                <Plus className="h-3.5 w-3.5" />
                Agregar ítem
              </Button>
            </div>

            <div className="space-y-2">
              {items.map((item, idx) => (
                <div
                  key={idx}
                  className="grid grid-cols-12 gap-2 rounded-md border p-2"
                >
                  <div className="col-span-12 sm:col-span-5">
                    <Select
                      value={item.productId}
                      onValueChange={(v) => onSelectProduct(idx, v)}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="Producto…" />
                      </SelectTrigger>
                      <SelectContent>
                        {products.map((p) => (
                          <SelectItem key={p.id} value={p.id}>
                            {p.name}
                            {p.category ? ` · ${p.category}` : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="col-span-3 sm:col-span-2">
                    <Input
                      type="number"
                      min={1}
                      inputMode="numeric"
                      value={item.quantity}
                      onChange={(e) =>
                        updateItem(idx, {
                          quantity: Math.max(1, Number(e.target.value) || 0),
                        })
                      }
                      aria-label="Cantidad"
                    />
                  </div>
                  <div className="col-span-6 sm:col-span-3">
                    <Input
                      type="number"
                      min={0}
                      inputMode="decimal"
                      value={item.unitPrice}
                      onChange={(e) =>
                        updateItem(idx, {
                          unitPrice: Math.max(0, Number(e.target.value) || 0),
                        })
                      }
                      aria-label="Precio unitario"
                    />
                  </div>
                  <div className="col-span-12 sm:col-span-2 flex items-center justify-between gap-2 sm:justify-end">
                    <span className="text-sm tabular-nums sm:hidden">
                      {formatCOP(item.quantity * item.unitPrice)}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        setItems((prev) =>
                          prev.length === 1
                            ? [emptyItem()]
                            : prev.filter((_, i) => i !== idx)
                        )
                      }
                      aria-label="Eliminar ítem"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="col-span-12">
                    <Input
                      placeholder="Notas del ítem (opcional)"
                      value={item.notes}
                      onChange={(e) =>
                        updateItem(idx, { notes: e.target.value })
                      }
                    />
                  </div>
                  <div className="col-span-12 hidden sm:flex justify-end text-xs text-muted-foreground tabular-nums">
                    {formatCOP(item.quantity * item.unitPrice)}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Delivery + payment */}
          <section className="grid grid-cols-2 gap-2">
            <div className="space-y-1.5 col-span-2">
              <Label htmlFor="address">Dirección</Label>
              <Textarea
                id="address"
                rows={2}
                value={deliveryAddress}
                onChange={(e) => setDeliveryAddress(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="phone">Teléfono</Label>
              <Input
                id="phone"
                inputMode="tel"
                value={contactPhone}
                onChange={(e) => setContactPhone(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="payment">Método de pago</Label>
              <Input
                id="payment"
                placeholder="Nequi, efectivo, …"
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="fee">Domicilio</Label>
              <Input
                id="fee"
                type="number"
                min={0}
                inputMode="decimal"
                value={deliveryFee}
                onChange={(e) =>
                  setDeliveryFee(Math.max(0, Number(e.target.value) || 0))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="orderNotes">Notas del pedido</Label>
              <Input
                id="orderNotes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
          </section>

          {/* Totals */}
          <div className="rounded-md border bg-muted/40 p-3 text-sm space-y-1">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Subtotal</span>
              <span className="tabular-nums">{formatCOP(subtotal)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Domicilio</span>
              <span className="tabular-nums">{formatCOP(deliveryFee)}</span>
            </div>
            <div className="flex justify-between font-medium border-t pt-1">
              <span>Total</span>
              <span className="tabular-nums">{formatCOP(total)}</span>
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={!canSubmit || isPending}>
              {isPending ? "Creando…" : "Crear pedido"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
