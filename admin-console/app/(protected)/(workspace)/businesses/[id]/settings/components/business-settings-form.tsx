"use client"

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Plus, X, Save, MapPin, CreditCard, Gift, Calendar, MessageSquare, Package } from "lucide-react"
import { toast } from "sonner"
import { updateBusinessSettings, BusinessSettings } from "@/lib/actions/business-settings"

const businessSettingsSchema = z.object({
  name: z.string().min(1, "El nombre del negocio es requerido"),
  business_type: z.string().min(1, "El tipo de negocio es requerido"),
  address: z.string().min(1, "La dirección es requerida"),
  phone: z.string().min(1, "El teléfono es requerido"),
  city: z.string().min(1, "La ciudad es requerida"),
  state: z.string().min(1, "El departamento es requerido"),
  country: z.string().min(1, "El país es requerido"),
  timezone: z.string().min(1, "La zona horaria es requerida"),
  language: z.string().min(1, "El idioma es requerido"),
  payment_methods: z.array(z.string()),
  promotions: z.array(z.string()),
  appointment_settings: z.object({
    max_concurrent: z.number().min(1, "Debe permitir al menos 1 cita simultánea"),
    min_advance_hours: z.number().min(0, "No puede ser negativo"),
    default_duration_minutes: z.number().min(1, "Debe ser al menos 1 minuto"),
  }),
  ai_prompt: z.string().min(1, "El prompt del asistente es requerido"),
  products_enabled: z.boolean(),
  menu_url: z.string().optional(),
  agent_enabled: z.boolean(),
})

type BusinessSettingsFormData = z.infer<typeof businessSettingsSchema>

interface BusinessSettingsFormProps {
  business: {
    id: string
    name: string
    business_type: string | null
    settings: unknown
  }
  initialSettings: BusinessSettings
  readOnly?: boolean
}

export function BusinessSettingsForm({ business, initialSettings, readOnly = false }: BusinessSettingsFormProps) {
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<BusinessSettingsFormData>({
    resolver: zodResolver(businessSettingsSchema),
    defaultValues: initialSettings,
  })

  const onSubmit = async (data: BusinessSettingsFormData) => {
    setIsLoading(true)
    try {
      const result = await updateBusinessSettings(business.id, data)
      if (result.success) {
        toast.success("¡Configuración guardada exitosamente!")
      } else {
        toast.error(result.error || "No se pudo actualizar la configuración")
      }
    } catch {
      toast.error("Ocurrió un error al actualizar la configuración")
    } finally {
      setIsLoading(false)
    }
  }

  // Helper functions for dynamic arrays
  const addPaymentMethod = () => {
    const currentMethods = form.getValues("payment_methods")
    form.setValue("payment_methods", [...currentMethods, ""])
  }

  const removePaymentMethod = (index: number) => {
    const currentMethods = form.getValues("payment_methods")
    form.setValue("payment_methods", currentMethods.filter((_, i) => i !== index))
  }

  const addPromotion = () => {
    const currentPromotions = form.getValues("promotions")
    form.setValue("promotions", [...currentPromotions, ""])
  }

  const removePromotion = (index: number) => {
    const currentPromotions = form.getValues("promotions")
    form.setValue("promotions", currentPromotions.filter((_, i) => i !== index))
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
      <fieldset disabled={readOnly} className="space-y-6">
      {/* Basic Information */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MapPin className="h-5 w-5" />
            Información básica
          </CardTitle>
          <CardDescription>
            Configura los detalles básicos de tu negocio
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
            <div className="space-y-1">
              <Label className="text-base font-medium">Agente IA</Label>
              <p className="text-sm text-muted-foreground">
                Cuando está apagado, los mensajes entrantes se guardan pero no se envían respuestas automáticas.
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Switch
                checked={form.watch("agent_enabled")}
                onCheckedChange={(checked) => form.setValue("agent_enabled", checked)}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="name">Nombre del negocio</Label>
              <Input
                id="name"
                {...form.register("name")}
                placeholder="Ingresa el nombre del negocio"
              />
              {form.formState.errors.name && (
                <p className="text-sm text-red-500">{form.formState.errors.name.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="business_type">Tipo de negocio</Label>
              <Select
                value={form.watch("business_type")}
                onValueChange={(value) => form.setValue("business_type", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecciona el tipo de negocio" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="barberia">Barbería</SelectItem>
                  <SelectItem value="salon">Salón de Belleza</SelectItem>
                  <SelectItem value="spa">Spa</SelectItem>
                  <SelectItem value="clinic">Clínica</SelectItem>
                  <SelectItem value="restaurant">Restaurante</SelectItem>
                  <SelectItem value="other">Otro</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="address">Dirección</Label>
              <Input
                id="address"
                {...form.register("address")}
                placeholder="Ingresa la dirección del negocio"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="phone">Teléfono</Label>
              <Input
                id="phone"
                {...form.register("phone")}
                placeholder="+57 300 123 4567"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="city">Ciudad</Label>
              <Input
                id="city"
                {...form.register("city")}
                placeholder="Ingresa la ciudad"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="state">Departamento/Provincia</Label>
              <Input
                id="state"
                {...form.register("state")}
                placeholder="Ingresa el departamento o provincia"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="country">País</Label>
              <Input
                id="country"
                {...form.register("country")}
                placeholder="Ingresa el país"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="timezone">Zona horaria</Label>
              <Select
                value={form.watch("timezone")}
                onValueChange={(value) => form.setValue("timezone", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecciona la zona horaria" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="America/Bogota">America/Bogota (Colombia)</SelectItem>
                  <SelectItem value="America/Mexico_City">America/Mexico_City (México)</SelectItem>
                  <SelectItem value="America/Argentina/Buenos_Aires">America/Argentina/Buenos_Aires (Argentina)</SelectItem>
                  <SelectItem value="America/Santiago">America/Santiago (Chile)</SelectItem>
                  <SelectItem value="America/Lima">America/Lima (Perú)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="language">Idioma</Label>
              <Select
                value={form.watch("language")}
                onValueChange={(value) => form.setValue("language", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecciona el idioma" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="es-CO">Español (Colombia)</SelectItem>
                  <SelectItem value="es-MX">Español (México)</SelectItem>
                  <SelectItem value="es-AR">Español (Argentina)</SelectItem>
                  <SelectItem value="es-CL">Español (Chile)</SelectItem>
                  <SelectItem value="es-PE">Español (Perú)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="md:col-span-2 flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="products_enabled" className="flex items-center gap-2">
                  <Package className="h-4 w-4" />
                  Habilitar pedidos de productos
                </Label>
                <p className="text-sm text-muted-foreground">
                  Permite a los clientes ver el menú y realizar pedidos por WhatsApp
                </p>
              </div>
              <Switch
                id="products_enabled"
                checked={form.watch("products_enabled")}
                onCheckedChange={(checked) => form.setValue("products_enabled", checked)}
              />
            </div>

            <div className="md:col-span-2 space-y-2">
              <Label htmlFor="menu_url">URL del menú</Label>
              <Input
                id="menu_url"
                {...form.register("menu_url")}
                placeholder="https://ejemplo.com/menu.html"
                type="url"
              />
              <p className="text-sm text-muted-foreground">
                Enlace al menú completo. El asistente lo incluirá en el saludo inicial.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Payment Methods */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="h-5 w-5" />
            Métodos de pago
          </CardTitle>
          <CardDescription>
            Lista los métodos de pago que aceptas
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("payment_methods").map((method, index) => (
              <div key={index} className="flex gap-4 items-center">
                <Input
                  {...form.register(`payment_methods.${index}`)}
                  placeholder="ej., Efectivo, Tarjeta, Nequi"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => removePaymentMethod(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addPaymentMethod}>
              <Plus className="mr-2 h-4 w-4" />
              Agregar método de pago
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Promotions */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gift className="h-5 w-5" />
            Promociones
          </CardTitle>
          <CardDescription>
            Define las ofertas especiales y promociones para tus clientes
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {form.watch("promotions").map((promotion, index) => (
              <div key={index} className="flex gap-4 items-center">
                <Textarea
                  {...form.register(`promotions.${index}`)}
                  placeholder="e.g., Cumpleañero feliz: 10% de descuento si cumples este mes"
                  rows={2}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => removePromotion(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button type="button" variant="outline" onClick={addPromotion}>
              <Plus className="mr-2 h-4 w-4" />
              Agregar promoción
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Appointment Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Calendar className="h-5 w-5" />
            Configuración de citas
          </CardTitle>
          <CardDescription>
            Configura cómo se gestionan las citas
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="max_concurrent">Máx. citas simultáneas</Label>
              <Input
                id="max_concurrent"
                type="number"
                {...form.register("appointment_settings.max_concurrent", { valueAsNumber: true })}
                placeholder="2"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="min_advance_hours">Horas mínimas de anticipación</Label>
              <Input
                id="min_advance_hours"
                type="number"
                {...form.register("appointment_settings.min_advance_hours", { valueAsNumber: true })}
                placeholder="1"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="default_duration_minutes">Duración predeterminada (minutos)</Label>
              <Input
                id="default_duration_minutes"
                type="number"
                {...form.register("appointment_settings.default_duration_minutes", { valueAsNumber: true })}
                placeholder="60"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI Prompt */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" />
            Prompt del asistente IA
          </CardTitle>
          <CardDescription>
            Personaliza cómo tu asistente IA se comunica con los clientes
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Label htmlFor="ai_prompt">Prompt de IA</Label>
            <Textarea
              id="ai_prompt"
              {...form.register("ai_prompt")}
              placeholder="Ingresa el prompt del asistente IA para tu negocio..."
              rows={10}
              className="font-mono text-sm"
            />
            {form.formState.errors.ai_prompt && (
              <p className="text-sm text-red-500">{form.formState.errors.ai_prompt.message}</p>
            )}
          </div>
        </CardContent>
      </Card>
      </fieldset>

      {/* Submit Button */}
      {!readOnly && (
        <div className="flex justify-end">
          <Button type="submit" disabled={isLoading}>
            <Save className="mr-2 h-4 w-4" />
            {isLoading ? "Guardando..." : "Guardar configuración"}
          </Button>
        </div>
      )}
    </form>
  )
}
