"use client"

import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { UserPlus } from "lucide-react"
import { toast } from "sonner"
import { inviteUserToBusiness } from "@/lib/actions/users"

const inviteSchema = z.object({
  email: z.string().email("Correo electrónico inválido"),
  full_name: z.string().min(1, "El nombre es requerido"),
  password: z.string().min(8, "La contraseña debe tener al menos 8 caracteres"),
  role: z.string(),
})

type InviteFormData = z.infer<typeof inviteSchema>

interface InviteUserButtonProps {
  businessId: string
  businessName: string
}

export function InviteUserButton({ businessId, businessName }: InviteUserButtonProps) {
  const [open, setOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<InviteFormData>({
    resolver: zodResolver(inviteSchema),
    defaultValues: {
      email: "",
      full_name: "",
      password: "",
      role: "member",
    },
  })

  const onSubmit = async (data: InviteFormData) => {
    setIsLoading(true)
    try {
      const result = await inviteUserToBusiness(businessId, data)
      if (result.success) {
        toast.success("¡Usuario invitado exitosamente!")
        setOpen(false)
        form.reset()
      } else {
        toast.error(result.error || "No se pudo invitar al usuario")
      }
    } catch {
      toast.error("Ocurrió un error")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus className="mr-2 h-4 w-4" />
          Invitar miembro del equipo
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invitar miembro del equipo</DialogTitle>
          <DialogDescription>
            Agrega un nuevo miembro del equipo a {businessName}. Si el usuario ya existe, será añadido a este negocio.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="full_name">Nombre completo</Label>
            <Input
              id="full_name"
              {...form.register("full_name")}
              placeholder="Ingresa el nombre completo"
            />
            {form.formState.errors.full_name && (
              <p className="text-sm text-red-500">{form.formState.errors.full_name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">Correo electrónico</Label>
            <Input
              id="email"
              type="email"
              {...form.register("email")}
              placeholder="usuario@ejemplo.com"
            />
            {form.formState.errors.email && (
              <p className="text-sm text-red-500">{form.formState.errors.email.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Contraseña</Label>
            <Input
              id="password"
              type="password"
              {...form.register("password")}
              placeholder="Mínimo 8 caracteres"
            />
            <p className="text-xs text-muted-foreground">
              Si el usuario ya existe, esta contraseña será ignorada
            </p>
            {form.formState.errors.password && (
              <p className="text-sm text-red-500">{form.formState.errors.password.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">Rol en este negocio</Label>
            <Select
              value={form.watch("role")}
              onValueChange={(value) => form.setValue("role", value)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Selecciona el rol" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="admin">Admin (puede editar configuración)</SelectItem>
                <SelectItem value="member">Miembro (solo lectura)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isLoading}>
              {isLoading ? "Invitando..." : "Invitar"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
