"use client"

import { useMemo, useState } from "react"
import { Plus, Pencil, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
  const [promoOnly, setPromoOnly] = useState(false)
  const [statusTab, setStatusTab] = useState<"active" | "inactive">("active")
  const [searchQuery, setSearchQuery] = useState("")
  const [categoryFilter, setCategoryFilter] = useState<string>("all")

  const categories = useMemo(() => {
    const set = new Set<string>()
    products.forEach((p) => {
      const c = p.category?.trim()
      if (c) set.add(c)
    })
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [products])

  const counts = useMemo(
    () => ({
      active: products.filter((p) => p.is_active).length,
      inactive: products.filter((p) => !p.is_active).length,
    }),
    [products]
  )

  const filtered = useMemo(() => {
    const wantActive = statusTab === "active"
    const q = searchQuery.trim().toLowerCase()
    return products
      .filter((p) => p.is_active === wantActive)
      .filter((p) =>
        categoryFilter === "all" ? true : (p.category ?? "") === categoryFilter
      )
      .filter((p) => {
        if (!q) return true
        return (
          p.name.toLowerCase().includes(q) ||
          (p.category ?? "").toLowerCase().includes(q)
        )
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [products, statusTab, searchQuery, categoryFilter])

  function openCreate() {
    setEditor({ mode: "create" })
    setName("")
    setDescription("")
    setSku("")
    setPrice("")
    setCategory("")
    setPromoOnly(false)
  }

  function openEdit(product: ProductRow) {
    setEditor({ mode: "edit", product })
    setName(product.name)
    setDescription(product.description ?? "")
    setSku(product.sku ?? "")
    setPrice(String(product.price))
    setCategory(product.category ?? "")
    setPromoOnly(product.promo_only)
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
          promo_only: promoOnly,
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
          promo_only: promoOnly,
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
      <Tabs
        value={statusTab}
        onValueChange={(v) => setStatusTab(v as "active" | "inactive")}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <TabsList>
            <TabsTrigger value="active">
              Activos
              <Badge variant="secondary" className="ml-2">
                {counts.active}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="inactive">
              Inactivos
              <Badge variant="secondary" className="ml-2">
                {counts.inactive}
              </Badge>
            </TabsTrigger>
          </TabsList>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4 mr-2" />
            Nuevo producto
          </Button>
        </div>
      </Tabs>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Buscar por nombre o categoría"
            className="pl-8"
          />
        </div>
        <Select value={categoryFilter} onValueChange={setCategoryFilter}>
          <SelectTrigger className="w-[200px]">
            <SelectValue placeholder="Categoría" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Todas las categorías</SelectItem>
            {categories.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="rounded-lg border overflow-hidden">
        <table className="w-full">
          <thead className="bg-muted">
            <tr>
              <th className="px-4 py-3 text-left text-sm font-medium">Producto</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Categoría</th>
              <th className="px-4 py-3 text-left text-sm font-medium">SKU</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Precio</th>
              <th className="px-4 py-3 text-left text-sm font-medium">Activo</th>
              <th className="px-4 py-3 text-right text-sm font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                  {products.length === 0
                    ? "Aún no hay productos. Crea el primero para tu catálogo."
                    : "No hay productos que coincidan con los filtros."}
                </td>
              </tr>
            ) : (
              filtered.map((product) => (
                <tr key={product.id} className="hover:bg-muted/30">
                  <td className="px-4 py-3 font-medium">
                    <div className="flex items-center gap-2">
                      <span>{product.name}</span>
                      {product.promo_only ? (
                        <Badge variant="outline" className="text-xs font-normal">
                          Solo en promos
                        </Badge>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {product.category || "—"}
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">
                    {product.sku || "—"}
                  </td>
                  <td className="px-4 py-3 text-sm">{formatPrice(product.price)}</td>
                  <td className="px-4 py-3">
                    <Switch
                      checked={product.is_active}
                      onCheckedChange={() => void handleToggle(product)}
                      aria-label={
                        product.is_active
                          ? "Desactivar producto"
                          : "Activar producto"
                      }
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => openEdit(product)}>
                        <Pencil className="h-4 w-4" />
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
            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="space-y-0.5 pr-3">
                <Label htmlFor="promo-only-switch" className="text-sm">
                  Solo en promociones
                </Label>
                <p className="text-xs text-muted-foreground">
                  El bot no lo ofrecerá individualmente. Disponible solo cuando
                  forme parte de una promo o combo.
                </p>
              </div>
              <Switch
                id="promo-only-switch"
                checked={promoOnly}
                onCheckedChange={setPromoOnly}
                aria-label="Solo en promociones"
              />
            </div>
          </div>
          <DialogFooter className="flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted-foreground sm:mr-auto">
              Al guardar regeneramos las etiquetas de búsqueda para que el bot
              encuentre el producto. Puede tardar unos segundos.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditor({ mode: "closed" })}>
                Cancelar
              </Button>
              <Button onClick={() => void handleSave()} disabled={saving}>
                {saving ? "Guardando y regenerando búsqueda..." : "Guardar"}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
