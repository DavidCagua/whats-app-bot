"use client"

import { useMemo, useState } from "react"
import { Plus, Pencil, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { toast } from "sonner"
import {
  deletePromotion,
  setPromotionActive,
  type SerializedPromotion,
} from "@/lib/actions/promotions"
import { PromotionFormDialog } from "./promotion-form-dialog"

type ProductOption = { id: string; name: string; price: number }

const DAY_LABELS: Record<number, string> = {
  1: "L",
  2: "Ma",
  3: "Mi",
  4: "J",
  5: "V",
  6: "S",
  7: "D",
}

const formatMoney = (value: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(value)

function pricingLabel(p: SerializedPromotion): string {
  if (p.fixed_price != null) return `Precio fijo ${formatMoney(p.fixed_price)}`
  if (p.discount_amount != null)
    return `Descuento ${formatMoney(p.discount_amount)}`
  if (p.discount_pct != null) return `${p.discount_pct}% off`
  return "—"
}

function scheduleLabel(p: SerializedPromotion): string {
  const parts: string[] = []
  if (p.days_of_week && p.days_of_week.length > 0) {
    const sorted = [...p.days_of_week].sort()
    parts.push(sorted.map((d) => DAY_LABELS[d] ?? "?").join(" "))
  }
  if (p.start_time && p.end_time) {
    parts.push(`${p.start_time}–${p.end_time}`)
  } else if (p.start_time) {
    parts.push(`desde ${p.start_time}`)
  } else if (p.end_time) {
    parts.push(`hasta ${p.end_time}`)
  }
  if (p.starts_on || p.ends_on) {
    parts.push(`${p.starts_on ?? "…"} → ${p.ends_on ?? "…"}`)
  }
  return parts.length === 0 ? "Siempre" : parts.join(" · ")
}

type EditorState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; promotion: SerializedPromotion }

export function PromotionsManager({
  businessId,
  initialPromotions,
  products,
}: {
  businessId: string
  initialPromotions: SerializedPromotion[]
  products: ProductOption[]
}) {
  const [promotions, setPromotions] = useState<SerializedPromotion[]>(
    initialPromotions
  )
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" })
  const [pendingDelete, setPendingDelete] = useState<SerializedPromotion | null>(
    null
  )
  const [busyId, setBusyId] = useState<string | null>(null)

  const sorted = useMemo(
    () =>
      [...promotions].sort((a, b) => {
        if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
        return a.name.localeCompare(b.name)
      }),
    [promotions]
  )

  async function handleToggleActive(promo: SerializedPromotion, next: boolean) {
    setBusyId(promo.id)
    try {
      const result = await setPromotionActive(promo.id, next)
      if (!result.success) throw new Error(result.error)
      setPromotions((prev) =>
        prev.map((p) => (p.id === promo.id ? result.promotion : p))
      )
      toast.success(next ? "Promo activada" : "Promo desactivada")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "No se pudo actualizar")
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete() {
    if (!pendingDelete) return
    setBusyId(pendingDelete.id)
    try {
      const result = await deletePromotion(pendingDelete.id)
      if (!result.success) throw new Error(result.error)
      setPromotions((prev) => prev.filter((p) => p.id !== pendingDelete.id))
      toast.success("Promo eliminada")
      setPendingDelete(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "No se pudo eliminar")
    } finally {
      setBusyId(null)
    }
  }

  function handleSaved(promo: SerializedPromotion) {
    setPromotions((prev) => {
      const exists = prev.some((p) => p.id === promo.id)
      return exists
        ? prev.map((p) => (p.id === promo.id ? promo : p))
        : [...prev, promo]
    })
    setEditor({ mode: "closed" })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {promotions.length === 0
            ? "Aún no hay promociones."
            : `${promotions.length} promo${promotions.length === 1 ? "" : "s"}`}
        </p>
        <Button onClick={() => setEditor({ mode: "create" })} size="sm">
          <Plus className="mr-2 h-4 w-4" />
          Nueva promo
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>Precio</TableHead>
              <TableHead>Componentes</TableHead>
              <TableHead>Horario</TableHead>
              <TableHead>Activa</TableHead>
              <TableHead className="w-24"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-center text-muted-foreground py-8"
                >
                  Crea tu primera promoción para empezar.
                </TableCell>
              </TableRow>
            ) : (
              sorted.map((p) => (
                <TableRow
                  key={p.id}
                  className={p.is_active ? "" : "opacity-60"}
                >
                  <TableCell>
                    <div className="font-medium">{p.name}</div>
                    {p.description ? (
                      <div className="text-xs text-muted-foreground">
                        {p.description}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{pricingLabel(p)}</Badge>
                  </TableCell>
                  <TableCell className="text-sm">
                    {p.components.length === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      p.components.map((c) => (
                        <div key={c.id}>
                          {c.quantity}× {c.product_name ?? c.product_id.slice(0, 8)}
                        </div>
                      ))
                    )}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {scheduleLabel(p)}
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={p.is_active}
                      disabled={busyId === p.id}
                      onCheckedChange={(next) => void handleToggleActive(p, next)}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setEditor({ mode: "edit", promotion: p })}
                        aria-label="Editar"
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setPendingDelete(p)}
                        aria-label="Eliminar"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <PromotionFormDialog
        open={editor.mode !== "closed"}
        businessId={businessId}
        products={products}
        promotion={editor.mode === "edit" ? editor.promotion : null}
        onClose={() => setEditor({ mode: "closed" })}
        onSaved={handleSaved}
      />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Eliminar promo</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.name} se eliminará permanentemente. Si ya fue
              aplicada en pedidos no se puede borrar — desactívala en vez.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault()
                void handleDelete()
              }}
            >
              Eliminar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
