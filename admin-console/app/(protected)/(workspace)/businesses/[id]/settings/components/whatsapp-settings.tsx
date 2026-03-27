"use client"

import { useState, useEffect, useCallback } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
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
import { Phone, Plus, Trash2, ExternalLink } from "lucide-react"
import { toast } from "sonner"
import {
  getWhatsAppNumbers,
  addWhatsAppNumber,
  deleteWhatsAppNumber,
  toggleWhatsAppNumberStatus,
  type WhatsAppNumber,
} from "@/lib/actions/whatsapp"

interface WhatsAppSettingsProps {
  businessId: string
}

export function WhatsAppSettings({ businessId }: WhatsAppSettingsProps) {
  const [numbers, setNumbers] = useState<WhatsAppNumber[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [showAddForm, setShowAddForm] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [numberToDelete, setNumberToDelete] = useState<string | null>(null)

  // Form state
  const [formData, setFormData] = useState({
    phoneNumberId: "",
    phoneNumber: "",
    displayName: "",
  })
  const [isSubmitting, setIsSubmitting] = useState(false)

  const loadNumbers = useCallback(async () => {
    setIsLoading(true)
    const result = await getWhatsAppNumbers(businessId)
    if (result.success) {
      setNumbers(result.numbers as WhatsAppNumber[])
    } else if (result.error) {
      toast.error(result.error)
    }
    setIsLoading(false)
  }, [businessId])

  useEffect(() => {
    loadNumbers()
  }, [loadNumbers])

  async function handleAddNumber(e: React.FormEvent) {
    e.preventDefault()
    setIsSubmitting(true)

    try {
      const result = await addWhatsAppNumber({
        businessId,
        phoneNumberId: formData.phoneNumberId.trim(),
        phoneNumber: formData.phoneNumber.trim(),
        displayName: formData.displayName.trim() || undefined,
      })

      if (result.success) {
        toast.success("¡Número de WhatsApp agregado exitosamente!")
        setFormData({ phoneNumberId: "", phoneNumber: "", displayName: "" })
        setShowAddForm(false)
        await loadNumbers()
      } else {
        toast.error(result.error || "No se pudo agregar el número de WhatsApp")
      }
    } catch (error) {
      toast.error("Ocurrió un error al agregar el número")
      console.error(error)
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleDeleteNumber() {
    if (!numberToDelete) return

    try {
      const result = await deleteWhatsAppNumber(numberToDelete)
      if (result.success) {
        toast.success("¡Número de WhatsApp eliminado exitosamente!")
        await loadNumbers()
      } else {
        toast.error(result.error || "No se pudo eliminar el número de WhatsApp")
      }
    } catch (error) {
      toast.error("Ocurrió un error al eliminar el número")
      console.error(error)
    } finally {
      setDeleteDialogOpen(false)
      setNumberToDelete(null)
    }
  }

  async function handleToggleStatus(id: string) {
    try {
      const result = await toggleWhatsAppNumberStatus(id)
      if (result.success) {
        toast.success("¡Estado actualizado exitosamente!")
        await loadNumbers()
      } else {
        toast.error(result.error || "No se pudo actualizar el estado")
      }
    } catch (error) {
      toast.error("Ocurrió un error al actualizar el estado")
      console.error(error)
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Phone className="h-5 w-5" />
            Configuración de WhatsApp
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Cargando...</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Phone className="h-5 w-5" />
            Configuración de WhatsApp
          </CardTitle>
          <CardDescription>
            Gestiona los números de WhatsApp Business para este negocio
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Help Documentation */}
          <div className="text-sm bg-blue-50 dark:bg-blue-950 p-4 rounded-lg space-y-2">
            <p className="font-semibold text-blue-900 dark:text-blue-100">
              Instrucciones para Super Admin:
            </p>
            <ol className="list-decimal pl-4 space-y-1 text-blue-800 dark:text-blue-200">
              <li>El dueño del negocio proporciona su número de WhatsApp Business</li>
              <li>Ingresa el número de teléfono abajo (con código de país, ej., +573001234567)</li>
              <li>
                <strong>Opcional:</strong> Si usas la API de WhatsApp Business de Meta, también puedes agregar el ID del número desde el Administrador de Meta Business para un enrutamiento más confiable
              </li>
            </ol>
            <a
              href="https://developers.facebook.com/docs/whatsapp/business-management-api/manage-phone-numbers"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1 mt-2"
            >
              Meta Documentation
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>

          {/* Existing Numbers */}
          {numbers.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold">Números configurados</h3>
              {numbers.map((number) => (
                <div
                  key={number.id}
                  className="flex items-center justify-between p-4 border rounded-lg"
                >
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{number.phone_number}</span>
                      {number.display_name && (
                        <span className="text-sm text-muted-foreground">
                          ({number.display_name})
                        </span>
                      )}
                      {number.is_active ? (
                        <Badge variant="default" className="ml-2">
                          Activo
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="ml-2">
                          Inactivo
                        </Badge>
                      )}
                    </div>
                    {number.phone_number_id && (
                      <p className="text-xs text-muted-foreground">
                        Meta ID: {number.phone_number_id}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-2">
                      <Label htmlFor={`active-${number.id}`} className="text-sm">
                        Activo
                      </Label>
                      <Switch
                        id={`active-${number.id}`}
                        checked={number.is_active}
                        onCheckedChange={() => handleToggleStatus(number.id)}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setNumberToDelete(number.id)
                        setDeleteDialogOpen(true)
                      }}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add Number Form */}
          {showAddForm ? (
            <form onSubmit={handleAddNumber} className="space-y-4 border p-4 rounded-lg">
              <div className="space-y-2">
                <Label htmlFor="phoneNumber">
                  Número de teléfono <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="phoneNumber"
                  placeholder="ej., +573001234567"
                  value={formData.phoneNumber}
                  onChange={(e) => setFormData({ ...formData, phoneNumber: e.target.value })}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  El número de WhatsApp Business (con código de país)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="displayName">Nombre de visualización (Opcional)</Label>
                <Input
                  id="displayName"
                  placeholder="ej., Línea principal"
                  value={formData.displayName}
                  onChange={(e) => setFormData({ ...formData, displayName: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Un nombre amigable para identificar este número
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="phoneNumberId">
                  ID del número de teléfono (Opcional - solo Meta)
                </Label>
                <Input
                  id="phoneNumberId"
                  placeholder="ej., 123456789012345"
                  value={formData.phoneNumberId}
                  onChange={(e) =>
                    setFormData({ ...formData, phoneNumberId: e.target.value })
                  }
                />
                <p className="text-xs text-muted-foreground">
                  El ID único del Administrador de Meta Business (15-20 dígitos). Solo necesario si usas la API de WhatsApp Business de Meta.
                </p>
              </div>

              <div className="flex gap-2">
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Agregando..." : "Agregar número"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setShowAddForm(false)
                    setFormData({ phoneNumberId: "", phoneNumber: "", displayName: "" })
                  }}
                >
                  Cancelar
                </Button>
              </div>
            </form>
          ) : (
            <Button onClick={() => setShowAddForm(true)} className="w-full">
              <Plus className="h-4 w-4 mr-2" />
              Agregar número de WhatsApp
            </Button>
          )}

          {numbers.length === 0 && !showAddForm && (
            <div className="text-center py-8 text-muted-foreground">
              <Phone className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p className="text-sm">Aún no hay números de WhatsApp configurados</p>
              <p className="text-xs mt-1">Agrega un número para habilitar el bot de WhatsApp en este negocio</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar número de WhatsApp?</AlertDialogTitle>
            <AlertDialogDescription>
              Esto eliminará el número de WhatsApp de este negocio. El bot dejará de
              responder mensajes enviados a este número. Esta acción no se puede deshacer.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setNumberToDelete(null)}>
              Cancelar
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteNumber} className="bg-destructive">
              Eliminar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
