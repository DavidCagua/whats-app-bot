"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Plus } from "lucide-react"
import { StaffFormDialog } from "./staff-form-dialog"

interface StaffFormProps {
  businessId: string
}

export function StaffForm({ businessId }: StaffFormProps) {
  const [open, setOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          Agregar personal
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Agregar personal</DialogTitle>
          <DialogDescription>
            Crea un nuevo miembro del personal para tu negocio
          </DialogDescription>
        </DialogHeader>
        <StaffFormDialog
          businessId={businessId}
          onClose={() => setOpen(false)}
          onSave={() => setOpen(false)}
        />
      </DialogContent>
    </Dialog>
  )
}
