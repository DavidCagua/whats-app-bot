"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ArrowLeft, Building2 } from "lucide-react"
import { toast } from "sonner"
import Link from "next/link"
import { createBusiness } from "@/lib/actions/business"

const createBusinessSchema = z.object({
  name: z.string().min(1, "El nombre del negocio es requerido"),
  business_type: z.string().min(1, "El tipo de negocio es requerido"),
})

type CreateBusinessFormData = z.infer<typeof createBusinessSchema>

export default function NewBusinessPage() {
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<CreateBusinessFormData>({
    resolver: zodResolver(createBusinessSchema),
    defaultValues: {
      name: "",
      business_type: "barberia",
    },
  })

  const onSubmit = async (data: CreateBusinessFormData) => {
    setIsLoading(true)
    try {
      const result = await createBusiness(data)
      if (result.success && result.businessId) {
        toast.success("¡Negocio creado exitosamente!")
        router.push(`/businesses/${result.businessId}/settings`)
      } else {
        toast.error(result.error || "No se pudo crear el negocio")
      }
    } catch {
      toast.error("Ocurrió un error al crear el negocio")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/businesses">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold">Crear negocio</h1>
          <p className="text-muted-foreground">
            Agrega un nuevo negocio a la plataforma
          </p>
        </div>
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Datos del negocio
          </CardTitle>
          <CardDescription>
            Ingresa la información básica del nuevo negocio
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                  <SelectItem value="barberia">Barberia</SelectItem>
                  <SelectItem value="salon">Salon de Belleza</SelectItem>
                  <SelectItem value="spa">Spa</SelectItem>
                  <SelectItem value="clinic">Clinica</SelectItem>
                  <SelectItem value="restaurant">Restaurante</SelectItem>
                  <SelectItem value="other">Otro</SelectItem>
                </SelectContent>
              </Select>
              {form.formState.errors.business_type && (
                <p className="text-sm text-red-500">{form.formState.errors.business_type.message}</p>
              )}
            </div>

            <div className="flex justify-end gap-4 pt-4">
              <Button variant="outline" asChild>
                <Link href="/businesses">Cancelar</Link>
              </Button>
              <Button type="submit" disabled={isLoading}>
                {isLoading ? "Creando..." : "Crear negocio"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
