"use client"

import { useMemo, useState } from "react"
import { Plus, Pencil, ToggleLeft, ToggleRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { toast } from "sonner"
import { createProduct, setProductActive, updateProduct, type SerializedProduct } from "@/lib/actions/products"

type ProductRow = SerializedProduct

type EditorState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; product: ProductRow }

const formatPrice = (value: number) =>
  new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(value)

export function ProductsManager({
  businessId,
  initialProducts,
}: {
  businessId: string
  initialProducts: ProductRow[]
}) {
  const [products, setProducts] = useState<ProductRow[]>(initialProducts)
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" })
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [sku, setSku] = useState("")
  const [price, setPrice] = useState("")
  const [category, setCategory] = useState("")

  const sorted = useMemo(
    () =>
      [...products].sort((a, b) => {
        if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
        return a.name.localeCompare(b.name)
      }),
    [products]
  )

  function openCreate() {
    setEditor({ mode: "create" })
    setName("")
    setDescription("")
    setSku("")
    setPrice("")
    setCategory("")
  }

  function openEdit(product: ProductRow) {
    setEditor({ mode: "edit", product })
    setName(product.name)
    setDescription(product.description ?? "")
    setSku(product.sku ?? "")
    setPrice(String(product.price))
    setCategory(product.category ?? "")
  }

  async function handleSave() {
    const parsedPrice = Number(price)
    if (!name.trim()) {
      toast.error("El nombre es requerido")
      return
    }
    if (!Number.isFinite(parsedPrice) || parsedPrice < 0) {
      toast.error("El precio debe ser un número válido")
      return
    }

    setSaving(true)
    try {
      if (editor.mode === "create") {
        const result = await createProduct(businessId, {
          name,
          description: description || null,
          sku: sku || null,
          price: parsedPrice,
          category: category || null,
        })
        if (!result.success) throw new Error(result.error)
        setProducts((prev) => [...prev, result.product])
        toast.success("Producto creado")
      } else if (editor.mode === "edit") {
        const result = await updateProduct(editor.product.id, {
          name,
          description: description || null,
          sku: sku || null,
          price: parsedPrice,
          category: category || null,
        })
        if (!result.success) throw new Error(result.error)
        const updated = result.product
        setProducts((prev) =>
          prev.map((item) => (item.id === updated.id ? updated : item))
        )
        toast.success("Producto actualizado")
      }
      setEditor({ mode: "closed" })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "No se pudo guardar")
    } finally {
      setSaving(false)
    }
  }

  async function handleToggle(product: ProductRow) {
    const result = await setProductActive(product.id, !product.is_active)
    if (!result.success) {
      toast.error(result.error || "No se pudo actualizar el estado")
      return
    }
    setProducts((prev) =>
      prev.map((item) =>
        item.id === product.id ? { ...item, is_active: !product.is_active } : item
      )
    )
    toast.success(!product.is_active ? "Producto activado" : "Producto desactivado")
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4 mr-2" />
          Nuevo producto
        </Button>
      </div>

      <div className="rounded-lg border overflow-hidden">
        <table className="w-full">
          <thead className="bg-muted">
            <tr>
              <th className="px-4 py-3 text-left text-sm font-medium">Producto</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Categoría</th>
              <th className="px-4 py-3 text-left text-sm font-medium">SKU</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Precio</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Estado</th>
              <th className="px-4 py-3 text-right text-sm font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                  Aún no hay productos. Crea el primero para tu catálogo.
                </td>
              </tr>
            ) : (
              sorted.map((product) => (
                <tr key={product.id} className="hover:bg-muted/30">
                  <td className="px-4 py-3 font-medium">{product.name}</td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {product.category || "—"}
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {product.sku || "—"}
                  </td>
                  <td className="px-4 py-3 text-sm">{formatPrice(product.price)}</td>
                  <td className="px-4 py-3">
                    {product.is_active ? (
                      <Badge variant="default">Activo</Badge>
                    ) : (
                      <Badge variant="secondary">Inactivo</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => openEdit(product)}>
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void handleToggle(product)}
                      >
                        {product.is_active ? (
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

      <Dialog
        open={editor.mode !== "closed"}
        onOpenChange={(open) => !open && setEditor({ mode: "closed" })}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editor.mode === "create" ? "Nuevo producto" : "Editar producto"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>Nombre</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Nombre del producto"
              />
            </div>
            <div className="space-y-1">
              <Label>Descripción</Label>
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Detalle del producto (opcional)"
                rows={3}
                className="resize-y min-h-[4.5rem]"
              />
            </div>
            <div className="space-y-1">
              <Label>Categoría</Label>
              <Input
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="ej. Camisetas, Zapatos (opcional)"
              />
            </div>
            <div className="space-y-1">
              <Label>SKU</Label>
              <Input
                value={sku}
                onChange={(e) => setSku(e.target.value)}
                placeholder="Opcional"
              />
            </div>
            <div className="space-y-1">
              <Label>Precio (COP)</Label>
              <Input
                type="number"
                min="0"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="0"
              />
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
