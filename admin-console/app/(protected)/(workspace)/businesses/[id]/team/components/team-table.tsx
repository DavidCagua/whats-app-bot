"use client"

import { useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { X } from "lucide-react"
import { toast } from "sonner"
import { removeUserFromBusiness } from "@/lib/actions/users"
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
} from "@/components/ui/alert-dialog"

interface TeamMember {
  id: string
  email: string
  full_name: string | null
  role: string | null
  is_active: boolean | null
  created_at: Date | null
}

interface TeamTableProps {
  data: TeamMember[]
  businessId: string
  canEdit: boolean
}

export function TeamTable({ data, businessId, canEdit }: TeamTableProps) {
  const [removingId, setRemovingId] = useState<string | null>(null)

  const handleRemove = async (userId: string) => {
    setRemovingId(userId)
    try {
      const result = await removeUserFromBusiness(userId, businessId)
      if (result.success) {
        toast.success("Miembro del equipo eliminado")
      } else {
        toast.error(result.error || "No se pudo eliminar al miembro del equipo")
      }
    } catch {
      toast.error("Ocurrió un error")
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Nombre</TableHead>
            <TableHead>Correo electrónico</TableHead>
            <TableHead>Rol</TableHead>
            <TableHead>Estado</TableHead>
            {canEdit && <TableHead className="text-right">Acciones</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.length === 0 ? (
            <TableRow>
              <TableCell colSpan={canEdit ? 5 : 4} className="text-center text-muted-foreground">
                Aún no hay miembros en el equipo
              </TableCell>
            </TableRow>
          ) : (
            data.map((member) => (
              <TableRow key={member.id}>
                <TableCell className="font-medium">
                  {member.full_name || "Sin nombre"}
                </TableCell>
                <TableCell>{member.email}</TableCell>
                <TableCell>
                  <Badge variant={member.role === "admin" ? "default" : "secondary"}>
                    {member.role || "member"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {member.is_active ? (
                    <Badge variant="default" className="bg-green-500">Activo</Badge>
                  ) : (
                    <Badge variant="secondary">Inactivo</Badge>
                  )}
                </TableCell>
                {canEdit && (
                  <TableCell className="text-right">
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          disabled={removingId === member.id}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>¿Eliminar miembro del equipo?</AlertDialogTitle>
                          <AlertDialogDescription>
                            Esto eliminará a <strong>{member.full_name || member.email}</strong> de este negocio.
                            Ya no tendrá acceso a este negocio.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancelar</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() => handleRemove(member.id)}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                          >
                            Eliminar
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </TableCell>
                )}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
