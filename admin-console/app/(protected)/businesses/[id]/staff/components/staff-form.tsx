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
          Add Staff Member
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Staff Member</DialogTitle>
          <DialogDescription>
            Create a new staff member for your business
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
