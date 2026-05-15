"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
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
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { deleteBusiness } from "@/lib/actions/business";

interface DeleteBusinessButtonProps {
  businessId: string;
  businessName: string;
}

export function DeleteBusinessButton({
  businessId,
  businessName,
}: DeleteBusinessButtonProps) {
  const router = useRouter();
  const [isDeleting, setIsDeleting] = useState(false);
  const [open, setOpen] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      const result = await deleteBusiness(businessId);
      if (result.success) {
        toast.success("Negocio eliminado exitosamente");
        router.push("/businesses");
      } else {
        toast.error(result.error || "No se pudo eliminar el negocio");
      }
    } catch {
      toast.error("Ocurrió un error al eliminar el negocio");
    } finally {
      setIsDeleting(false);
      setOpen(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive">
          <Trash2 className="mr-2 h-4 w-4" />
          Eliminar negocio
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>¿Estás seguro?</AlertDialogTitle>
          <AlertDialogDescription>
            Esto eliminará permanentemente <strong>{businessName}</strong> y
            todos los datos asociados, incluyendo:
            <ul className="list-disc list-inside mt-2 space-y-1">
              <li>Configuraciones de números de WhatsApp</li>
              <li>Historial de conversaciones</li>
              <li>Asignaciones de usuarios</li>
            </ul>
            <p className="mt-2 text-red-600 font-medium">
              Esta acción no se puede deshacer.
            </p>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            disabled={isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? "Eliminando..." : "Eliminar negocio"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
