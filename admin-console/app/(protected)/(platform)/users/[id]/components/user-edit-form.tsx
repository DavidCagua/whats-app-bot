"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { User, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { updateUser, deleteUser } from "@/lib/actions/users";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

const userEditSchema = z.object({
  email: z.string().email("Correo electrónico inválido"),
  full_name: z.string().min(1, "El nombre completo es requerido"),
  role: z.string(),
  is_active: z.boolean(),
  password: z.string().optional(),
});

type UserEditFormData = z.infer<typeof userEditSchema>;

interface UserEditFormProps {
  user: {
    id: string;
    email: string;
    full_name: string;
    role: string | null;
    is_active: boolean;
  };
  onRoleChange?: (role: string) => void;
}

export function UserEditForm({ user, onRoleChange }: UserEditFormProps) {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const form = useForm<UserEditFormData>({
    resolver: zodResolver(userEditSchema),
    defaultValues: {
      email: user.email,
      full_name: user.full_name,
      role: user.role || "business_user",
      is_active: user.is_active,
      password: "",
    },
  });

  const onSubmit = async (data: UserEditFormData) => {
    setIsLoading(true);
    try {
      const result = await updateUser(user.id, {
        email: data.email,
        full_name: data.full_name,
        role: data.role === "super_admin" ? "super_admin" : null,
        is_active: data.is_active,
        password: data.password || undefined,
      });

      if (result.success) {
        toast.success("¡Usuario actualizado exitosamente!");
      } else {
        toast.error(result.error || "No se pudo actualizar el usuario");
      }
    } catch {
      toast.error("Ocurrió un error al actualizar el usuario");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      const result = await deleteUser(user.id);
      if (result.success) {
        toast.success("Usuario eliminado exitosamente");
        router.push("/users");
      } else {
        toast.error(result.error || "No se pudo eliminar el usuario");
      }
    } catch {
      toast.error("Ocurrió un error al eliminar el usuario");
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <User className="h-5 w-5" />
          Datos del usuario
        </CardTitle>
        <CardDescription>
          Actualiza la información del usuario y el rol en el sistema
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="full_name">Nombre completo</Label>
            <Input id="full_name" {...form.register("full_name")} />
            {form.formState.errors.full_name && (
              <p className="text-sm text-red-500">
                {form.formState.errors.full_name.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">Correo electrónico</Label>
            <Input id="email" type="email" {...form.register("email")} />
            {form.formState.errors.email && (
              <p className="text-sm text-red-500">
                {form.formState.errors.email.message}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">
              Nueva contraseña (dejar en blanco para mantener la actual)
            </Label>
            <Input
              id="password"
              type="password"
              {...form.register("password")}
              placeholder="Ingresa la nueva contraseña"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">Rol en el sistema</Label>
            <Select
              value={form.watch("role")}
              onValueChange={(value) => {
                form.setValue("role", value);
                onRoleChange?.(value);
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Selecciona el rol" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="business_user">
                  Usuario de negocio
                </SelectItem>
                <SelectItem value="super_admin">
                  Súper Admin (Equipo OmnIA)
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between rounded-lg border p-4">
            <div className="space-y-0.5">
              <Label htmlFor="is_active">Estado de la cuenta</Label>
              <p className="text-sm text-muted-foreground">
                Los usuarios inactivos no pueden iniciar sesión
              </p>
            </div>
            <Switch
              id="is_active"
              checked={form.watch("is_active")}
              onCheckedChange={(checked) => form.setValue("is_active", checked)}
            />
          </div>

          <div className="flex justify-between pt-4">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" type="button">
                  <Trash2 className="mr-2 h-4 w-4" />
                  Eliminar usuario
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Eliminar usuario?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Esto eliminará permanentemente a{" "}
                    <strong>{user.full_name || user.email}</strong> y todas sus
                    asignaciones de negocio. Esta acción no se puede deshacer.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleDelete}
                    disabled={isDeleting}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    {isDeleting ? "Eliminando..." : "Eliminar usuario"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>

            <Button type="submit" disabled={isLoading}>
              <Save className="mr-2 h-4 w-4" />
              {isLoading ? "Guardando..." : "Guardar cambios"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
