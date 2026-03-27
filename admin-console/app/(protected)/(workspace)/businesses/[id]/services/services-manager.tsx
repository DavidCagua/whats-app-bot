"use client"

import { useMemo, useState } from "react"
import { Plus, Pencil, ToggleLeft, ToggleRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { createService, setServiceActive, updateService } from "@/lib/actions/services"

type ServiceRow = {
  id: string
  business_id: string
  name: string
  description: string | null
  price: number
  currency: string
  duration_minutes: number
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

type EditorState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; service: ServiceRow }

export function ServicesManager({
  businessId,
  initialServices,
}: {
  businessId: string
  initialServices: ServiceRow[]
}) {
  const [services, setServices] = useState<ServiceRow[]>(initialServices)
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" })
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [price, setPrice] = useState("")
  const [currency, setCurrency] = useState("COP")
  const [duration, setDuration] = useState("60")

  const sorted = useMemo(
    () =>
      [...services].sort((a, b) => {
        if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
        return a.name.localeCompare(b.name)
      }),
    [services]
  )

  function openCreate() {
    setEditor({ mode: "create" })
    setName("")
    setDescription("")
    setPrice("")
    setCurrency("COP")
    setDuration("60")
  }

  function openEdit(service: ServiceRow) {
    setEditor({ mode: "edit", service })
    setName(service.name)
    setDescription(service.description ?? "")
    setPrice(String(service.price))
    setCurrency(service.currency)
    setDuration(String(service.duration_minutes))
  }

  async function handleSave() {
    const parsedPrice = Number(price)
    const parsedDuration = Number(duration)
    if (!name.trim()) {
      toast.error("El nombre es requerido")
      return
    }
    if (!Number.isFinite(parsedPrice) || parsedPrice < 0) {
      toast.error("El precio debe ser un número válido")
      return
    }
    if (!Number.isInteger(parsedDuration) || parsedDuration <= 0) {
      toast.error("La duración debe ser un entero positivo")
      return
    }

    setSaving(true)
    try {
      if (editor.mode === "create") {
        const result = await createService(businessId, {
          name,
          description,
          price: parsedPrice,
          currency,
          duration_minutes: parsedDuration,
        })
        if (!result.success) throw new Error(result.error)
        const service = result.service
        setServices((prev) => [
          ...prev,
          {
            id: service.id,
            business_id: service.business_id,
            name: service.name,
            description: service.description,
            price: Number(service.price.toString()),
            currency: service.currency ?? "COP",
            duration_minutes: service.duration_minutes,
            is_active: service.is_active ?? true,
            created_at: service.created_at ? service.created_at.toISOString() : null,
            updated_at: service.updated_at ? service.updated_at.toISOString() : null,
          },
        ])
        toast.success("Servicio creado")
      } else if (editor.mode === "edit") {
        const result = await updateService(editor.service.id, {
          name,
          description,
          price: parsedPrice,
          currency,
          duration_minutes: parsedDuration,
        })
        if (!result.success) throw new Error(result.error)
        const updated = result.service
        setServices((prev) =>
          prev.map((item) =>
            item.id === updated.id
              ? {
                  ...item,
                  name: updated.name,
                  description: updated.description,
                  price: Number(updated.price.toString()),
                  currency: updated.currency ?? "COP",
                  duration_minutes: updated.duration_minutes,
                  is_active: updated.is_active ?? true,
                  updated_at: updated.updated_at ? updated.updated_at.toISOString() : null,
                }
              : item
          )
        )
        toast.success("Servicio actualizado")
      }
      setEditor({ mode: "closed" })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "No se pudo guardar")
    } finally {
      setSaving(false)
    }
  }

  async function handleToggle(service: ServiceRow) {
    const result = await setServiceActive(service.id, !service.is_active)
    if (!result.success) {
      toast.error(result.error || "No se pudo actualizar el estado")
      return
    }
    setServices((prev) =>
      prev.map((item) =>
        item.id === service.id ? { ...item, is_active: !service.is_active } : item
      )
    )
    toast.success(!service.is_active ? "Servicio activado" : "Servicio desactivado")
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />
          Nuevo servicio
        </Button>
      </div>

      <div className="rounded-lg border overflow-hidden">
        <table className="w-full">
          <thead className="bg-muted">
            <tr>
              <th className="px-4 py-3 text-left text-sm font-medium">Servicio</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Duración</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Precio</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Estado</th>
              <th className="px-4 py-3 text-right text-sm font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                  Aún no hay servicios. Crea el primero para reservas.
                </td>
              </tr>
            ) : (
              sorted.map((service) => (
                <tr key={service.id} className="hover:bg-muted/30">
                  <td className="px-4 py-3">
                    <div className="font-medium">{service.name}</div>
                    {service.description ? (
                      <div className="text-xs text-muted-foreground">{service.description}</div>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 text-sm">{service.duration_minutes} min</td>
                  <td className="px-4 py-3 text-sm">
                    {new Intl.NumberFormat("es-CO", {
                      style: "currency",
                      currency: service.currency || "COP",
                      minimumFractionDigits: 0,
                    }).format(service.price)}
                  </td>
                  <td className="px-4 py-3">
                    {service.is_active ? (
                      <Badge variant="default">Activo</Badge>
                    ) : (
                      <Badge variant="secondary">Inactivo</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => openEdit(service)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => void handleToggle(service)}>
                        {service.is_active ? (
                          <ToggleRight className="h-4 w-4" />
                        ) : (
                          <ToggleLeft className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Dialog open={editor.mode !== "closed"} onOpenChange={(open) => !open && setEditor({ mode: "closed" })}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editor.mode === "create" ? "Nuevo servicio" : "Editar servicio"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>Nombre</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Corte de cabello" />
            </div>
            <div className="space-y-1">
              <Label>Descripción</Label>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Opcional"
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="space-y-1">
                <Label>Precio</Label>
                <Input type="number" min="0" value={price} onChange={(e) => setPrice(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Moneda</Label>
                <Input value={currency} onChange={(e) => setCurrency(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>Duración (min)</Label>
                <Input type="number" min="1" value={duration} onChange={(e) => setDuration(e.target.value)} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditor({ mode: "closed" })}>
              Cancelar
            </Button>
            <Button onClick={() => void handleSave()} disabled={saving}>
              {saving ? "Guardando..." : "Guardar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
