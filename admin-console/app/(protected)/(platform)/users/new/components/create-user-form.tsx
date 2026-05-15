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
import { Badge } from "@/components/ui/badge";
import { ArrowLeft, UserPlus, Building2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";
import { createUser, assignUserToBusiness } from "@/lib/actions/users";

const createUserSchema = z.object({
  email: z.string().email("Correo electrónico inválido"),
  password: z.string().min(8, "La contraseña debe tener al menos 8 caracteres"),
  full_name: z.string().min(1, "El nombre completo es requerido"),
  role: z.string(),
});

type CreateUserFormData = z.infer<typeof createUserSchema>;

interface BusinessAssignment {
  businessId: string;
  businessName: string;
  role: string;
}

interface CreateUserFormProps {
  availableBusinesses: Array<{
    id: string;
    name: string;
  }>;
}

export function CreateUserForm({ availableBusinesses }: CreateUserFormProps) {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const [businessAssignments, setBusinessAssignments] = useState<
    BusinessAssignment[]
  >([]);
  const [selectedBusiness, setSelectedBusiness] = useState("");
  const [selectedBusinessRole, setSelectedBusinessRole] = useState("member");

  const form = useForm<CreateUserFormData>({
    resolver: zodResolver(createUserSchema),
    defaultValues: {
      email: "",
      password: "",
      full_name: "",
      role: "business_user",
    },
  });

  const watchRole = form.watch("role");
  const isBusinessUser = watchRole !== "super_admin";

  // Filter out already assigned businesses
  const unassignedBusinesses = availableBusinesses.filter(
    (b) => !businessAssignments.some((ba) => ba.businessId === b.id),
  );

  const addBusinessAssignment = () => {
    if (!selectedBusiness) return;

    const business = availableBusinesses.find((b) => b.id === selectedBusiness);
    if (!business) return;

    setBusinessAssignments([
      ...businessAssignments,
      {
        businessId: business.id,
        businessName: business.name,
        role: selectedBusinessRole,
      },
    ]);
    setSelectedBusiness("");
    setSelectedBusinessRole("member");
  };

  const removeBusinessAssignment = (businessId: string) => {
    setBusinessAssignments(
      businessAssignments.filter((ba) => ba.businessId !== businessId),
    );
  };

  const onSubmit = async (data: CreateUserFormData) => {
    // Validate business assignments for business users
    if (isBusinessUser && businessAssignments.length === 0) {
      toast.error(
        "Los usuarios de negocio deben estar asignados a al menos un negocio",
      );
      return;
    }

    setIsLoading(true);
    try {
      const result = await createUser({
        email: data.email,
        password: data.password,
        full_name: data.full_name,
        role: data.role === "super_admin" ? "super_admin" : null,
      });

      if (result.success && result.userId) {
        // Assign businesses for non-super-admin users
        if (isBusinessUser && businessAssignments.length > 0) {
          for (const assignment of businessAssignments) {
            await assignUserToBusiness(
              result.userId,
              assignment.businessId,
              assignment.role,
            );
          }
        }

        toast.success("¡Usuario creado exitosamente!");
        router.push("/users");
      } else {
        toast.error(result.error || "No se pudo crear el usuario");
      }
    } catch {
      toast.error("Ocurrió un error al crear el usuario");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/users">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold">Crear usuario</h1>
          <p className="text-muted-foreground">
            Agrega un nuevo usuario al sistema
          </p>
        </div>
      </div>

      <form onSubmit={form.handleSubmit(onSubmit)}>
        <div className={`grid gap-6 ${isBusinessUser ? "lg:grid-cols-2" : ""}`}>
          {/* User Details Card */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <UserPlus className="h-5 w-5" />
                Datos del usuario
              </CardTitle>
              <CardDescription>
                Ingresa la información del nuevo usuario
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="full_name">Nombre completo</Label>
                <Input
                  id="full_name"
                  {...form.register("full_name")}
                  placeholder="Ingresa el nombre completo"
                />
                {form.formState.errors.full_name && (
                  <p className="text-sm text-red-500">
                    {form.formState.errors.full_name.message}
                  </p>
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
                  <p className="text-sm text-red-500">
                    {form.formState.errors.email.message}
                  </p>
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
                {form.formState.errors.password && (
                  <p className="text-sm text-red-500">
                    {form.formState.errors.password.message}
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="role">Rol en el sistema</Label>
                <Select
                  value={form.watch("role")}
                  onValueChange={(value) => {
                    form.setValue("role", value);
                    // Clear business assignments when switching to super admin
                    if (value === "super_admin") {
                      setBusinessAssignments([]);
                    }
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
                <p className="text-sm text-muted-foreground">
                  {watchRole === "super_admin"
                    ? "Los súper admins tienen acceso total a todos los negocios"
                    : "Los usuarios de negocio deben ser asignados a negocios específicos"}
                </p>
              </div>
            </CardContent>
          </Card>

          {/* Business Assignments Card - Only for business users */}
          {isBusinessUser && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Building2 className="h-5 w-5" />
                  Asignaciones de negocio
                </CardTitle>
                <CardDescription>
                  Asigna el usuario a los negocios a los que puede acceder
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Current assignments */}
                <div className="space-y-2">
                  {businessAssignments.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center border rounded-lg">
                      Aún no hay negocios asignados. Agrega al menos uno.
                    </p>
                  ) : (
                    businessAssignments.map((assignment) => (
                      <div
                        key={assignment.businessId}
                        className="flex items-center justify-between rounded-lg border p-3"
                      >
                        <div className="flex items-center gap-3">
                          <span className="font-medium">
                            {assignment.businessName}
                          </span>
                          <Badge
                            variant={
                              assignment.role === "admin"
                                ? "default"
                                : "secondary"
                            }
                          >
                            {assignment.role}
                          </Badge>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() =>
                            removeBusinessAssignment(assignment.businessId)
                          }
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ))
                  )}
                </div>

                {/* Add new assignment */}
                {unassignedBusinesses.length > 0 && (
                  <div className="border-t pt-4 space-y-3">
                    <p className="text-sm font-medium">Agregar negocio</p>
                    <div className="flex gap-2">
                      <Select
                        value={selectedBusiness}
                        onValueChange={setSelectedBusiness}
                      >
                        <SelectTrigger className="flex-1">
                          <SelectValue placeholder="Selecciona el negocio" />
                        </SelectTrigger>
                        <SelectContent>
                          {unassignedBusinesses.map((business) => (
                            <SelectItem key={business.id} value={business.id}>
                              {business.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>

                      <Select
                        value={selectedBusinessRole}
                        onValueChange={setSelectedBusinessRole}
                      >
                        <SelectTrigger className="w-32">
                          <SelectValue placeholder="Rol" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="admin">Admin</SelectItem>
                          <SelectItem value="member">Miembro</SelectItem>
                        </SelectContent>
                      </Select>

                      <Button
                        type="button"
                        onClick={addBusinessAssignment}
                        disabled={!selectedBusiness}
                      >
                        <Plus className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Submit buttons */}
        <div className="flex justify-end gap-4 mt-6">
          <Button variant="outline" asChild>
            <Link href="/users">Cancelar</Link>
          </Button>
          <Button type="submit" disabled={isLoading}>
            {isLoading ? "Creando..." : "Crear usuario"}
          </Button>
        </div>
      </form>
    </div>
  );
}
