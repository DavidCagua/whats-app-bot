"use client"

import { useEffect, useMemo, useState } from "react"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"
import {
  createPromotion,
  updatePromotion,
  type PricingMode,
  type PromotionInput,
  type SerializedPromotion,
} from "@/lib/actions/promotions"

type ProductOption = { id: string; name: string; price: number }

const DAYS = [
  { iso: 1, label: "L" },
  { iso: 2, label: "Ma" },
  { iso: 3, label: "Mi" },
  { iso: 4, label: "J" },
  { iso: 5, label: "V" },
  { iso: 6, label: "S" },
  { iso: 7, label: "D" },
] as const

type ComponentRow = { product_id: string; quantity: number }

export function PromotionFormDialog({
  open,
  businessId,
  products,
  promotion,
  onClose,
  onSaved,
}: {
  open: boolean
  businessId: string
  products: ProductOption[]
  promotion: SerializedPromotion | null
  onClose: () => void
  onSaved: (promo: SerializedPromotion) => void
}) {
  const isEdit = promotion !== null

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [isActive, setIsActive] = useState(true)
  const [pricingMode, setPricingMode] = useState<PricingMode>("fixed_price")
  const [priceValue, setPriceValue] = useState("") // shared input across modes
  const [days, setDays] = useState<number[]>([])
  const [startTime, setStartTime] = useState("")
  const [endTime, setEndTime] = useState("")
  const [startsOn, setStartsOn] = useState("")
  const [endsOn, setEndsOn] = useState("")
  const [components, setComponents] = useState<ComponentRow[]>([
    { product_id: "", quantity: 1 },
  ])
  const [saving, setSaving] = useState(false)

  // Hydrate / reset form when the dialog opens or the editing target changes.
  useEffect(() => {
    if (!open) return
    if (promotion) {
      setName(promotion.name)
      setDescription(promotion.description ?? "")
      setIsActive(promotion.is_active)
      if (promotion.fixed_price != null) {
        setPricingMode("fixed_price")
        setPriceValue(String(promotion.fixed_price))
      } else if (promotion.discount_amount != null) {
        setPricingMode("discount_amount")
        setPriceValue(String(promotion.discount_amount))
      } else if (promotion.discount_pct != null) {
        setPricingMode("discount_pct")
        setPriceValue(String(promotion.discount_pct))
      } else {
        setPricingMode("fixed_price")
        setPriceValue("")
      }
      setDays(promotion.days_of_week ?? [])
      setStartTime(promotion.start_time ?? "")
      setEndTime(promotion.end_time ?? "")
      setStartsOn(promotion.starts_on ?? "")
      setEndsOn(promotion.ends_on ?? "")
      setComponents(
        promotion.components.length > 0
          ? promotion.components.map((c) => ({
              product_id: c.product_id,
              quantity: c.quantity,
            }))
          : [{ product_id: "", quantity: 1 }]
      )
    } else {
      setName("")
      setDescription("")
      setIsActive(true)
      setPricingMode("fixed_price")
      setPriceValue("")
      setDays([])
      setStartTime("")
      setEndTime("")
      setStartsOn("")
      setEndsOn("")
      setComponents([{ product_id: "", quantity: 1 }])
    }
  }, [open, promotion])

  const productMap = useMemo(
    () => new Map(products.map((p) => [p.id, p])),
    [products]
  )

  function toggleDay(iso: number) {
    setDays((prev) =>
      prev.includes(iso) ? prev.filter((d) => d !== iso) : [...prev, iso].sort()
    )
  }

  function updateComponent(idx: number, patch: Partial<ComponentRow>) {
    setComponents((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)))
  }

  function addComponent() {
    setComponents((prev) => [...prev, { product_id: "", quantity: 1 }])
  }

  function removeComponent(idx: number) {
    setComponents((prev) => prev.filter((_, i) => i !== idx))
  }

  async function handleSave() {
    const numericPrice = Number(priceValue)
    if (Number.isNaN(numericPrice)) {
      toast.error("El valor del precio no es válido.")
      return
    }
    if (components.some((c) => !c.product_id)) {
      toast.error("Selecciona un producto en cada componente.")
      return
    }
    if (components.some((c) => c.quantity < 1 || !Number.isInteger(c.quantity))) {
      toast.error("La cantidad debe ser un entero positivo.")
      return
    }

    const input: PromotionInput = {
      name,
      description: description || null,
      is_active: isActive,
      fixed_price: pricingMode === "fixed_price" ? numericPrice : null,
      discount_amount: pricingMode === "discount_amount" ? numericPrice : null,
      discount_pct:
        pricingMode === "discount_pct" ? Math.round(numericPrice) : null,
      days_of_week: days.length > 0 ? days : null,
      start_time: startTime || null,
      end_time: endTime || null,
      starts_on: startsOn || null,
      ends_on: endsOn || null,
      components: components.map((c) => ({
        product_id: c.product_id,
        quantity: c.quantity,
      })),
    }

    setSaving(true)
    try {
      const result = isEdit
        ? await updatePromotion(promotion!.id, input)
        : await createPromotion(businessId, input)
      if (!result.success) throw new Error(result.error)
      toast.success(isEdit ? "Promo actualizada" : "Promo creada")
      onSaved(result.promotion)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Error al guardar")
    } finally {
      setSaving(false)
    }
  }

  const priceLabel: Record<PricingMode, string> = {
    fixed_price: "Precio fijo (COP)",
    discount_amount: "Descuento (COP)",
    discount_pct: "Descuento (%)",
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar promo" : "Nueva promo"}</DialogTitle>
          <DialogDescription>
            Define el precio, los productos requeridos y el horario.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="promo-name">Nombre</Label>
            <Input
              id="promo-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="2 Honey Burger con papas"
              maxLength={120}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="promo-desc">Descripción (opcional)</Label>
            <Textarea
              id="promo-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Detalles que verá el cliente"
              rows={2}
            />
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <div className="text-sm font-medium">Activa</div>
              <div className="text-xs text-muted-foreground">
                Si está apagada, el bot no la ofrece ni la aplica.
              </div>
            </div>
            <Switch checked={isActive} onCheckedChange={setIsActive} />
          </div>

          {/* Pricing mode */}
          <div className="space-y-2">
            <Label>Tipo de precio</Label>
            <div className="grid grid-cols-3 gap-2">
              {(["fixed_price", "discount_amount", "discount_pct"] as const).map(
                (mode) => (
                  <Button
                    key={mode}
                    type="button"
                    variant={pricingMode === mode ? "default" : "outline"}
                    size="sm"
                    onClick={() => setPricingMode(mode)}
                  >
                    {mode === "fixed_price"
                      ? "Precio fijo"
                      : mode === "discount_amount"
                      ? "Descuento $"
                      : "Descuento %"}
                  </Button>
                )
              )}
            </div>
            <Input
              type="number"
              inputMode="numeric"
              min={0}
              step={pricingMode === "discount_pct" ? 1 : 100}
              value={priceValue}
              onChange={(e) => setPriceValue(e.target.value)}
              placeholder={priceLabel[pricingMode]}
            />
          </div>

          {/* Schedule */}
          <div className="space-y-3 rounded-md border p-3">
            <div className="text-sm font-medium">Horario (opcional)</div>
            <div className="space-y-2">
              <Label className="text-xs">Días de la semana</Label>
              <div className="flex flex-wrap gap-2">
                {DAYS.map((d) => {
                  const selected = days.includes(d.iso)
                  return (
                    <Button
                      key={d.iso}
                      type="button"
                      variant={selected ? "default" : "outline"}
                      size="sm"
                      className="w-10"
                      onClick={() => toggleDay(d.iso)}
                    >
                      {d.label}
                    </Button>
                  )
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                Sin selección = todos los días.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="promo-start-time" className="text-xs">
                  Hora inicio
                </Label>
                <Input
                  id="promo-start-time"
                  type="time"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="promo-end-time" className="text-xs">
                  Hora fin
                </Label>
                <Input
                  id="promo-end-time"
                  type="time"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="promo-starts-on" className="text-xs">
                  Vigencia desde
                </Label>
                <Input
                  id="promo-starts-on"
                  type="date"
                  value={startsOn}
                  onChange={(e) => setStartsOn(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="promo-ends-on" className="text-xs">
                  Vigencia hasta
                </Label>
                <Input
                  id="promo-ends-on"
                  type="date"
                  value={endsOn}
                  onChange={(e) => setEndsOn(e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Components */}
          <div className="space-y-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">Productos requeridos</div>
                <div className="text-xs text-muted-foreground">
                  El carrito debe contener todos estos para que aplique.
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={addComponent}
              >
                <Plus className="mr-1 h-3 w-3" />
                Agregar
              </Button>
            </div>
            <div className="space-y-2">
              {components.map((c, idx) => (
                <div key={idx} className="flex gap-2">
                  <Input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    step={1}
                    className="w-20"
                    value={c.quantity}
                    onChange={(e) =>
                      updateComponent(idx, {
                        quantity: Math.max(1, Number(e.target.value) || 1),
                      })
                    }
                  />
                  <Select
                    value={c.product_id}
                    onValueChange={(v) => updateComponent(idx, { product_id: v })}
                  >
                    <SelectTrigger className="flex-1">
                      <SelectValue placeholder="Selecciona un producto" />
                    </SelectTrigger>
                    <SelectContent>
                      {products.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => removeComponent(idx)}
                    disabled={components.length === 1}
                    aria-label="Eliminar componente"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? "Guardando..." : isEdit ? "Guardar cambios" : "Crear promo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
