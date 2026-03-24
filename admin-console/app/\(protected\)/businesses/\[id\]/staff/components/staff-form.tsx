"use client"

import { useState, ReactNode } from "react"
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
import { Switch } from "@/components/ui/switch"
import { Plus } from "lucide-react"
import { toast } from "sonner"
import { createStaffMember, updateStaffMember } from "@/lib/actions/staff"

const staffSchema = z.object({
  name: z.string().min(1, "Name is required"),
  role: z.string().min(1, "Role is required"),
  is_active: z.boolean().default(true),
  user_id: z.string().optional().nullable(),
})

type StaffFormData = z.infer<typeof staffSchema>

interface StaffMember {
  id: string
  name: string
  role: string
  is_active: boolean
  user_id: string | null
  user: {
    id: string
    email: string
    full_name: string | null
  } | null
}

interface StaffFormDialogProps {
  businessId: string
  editingStaff?: StaffMember | null
  availableUsers: Array<{ id: string; email: string; full_name: string | null }>
  children?: ReactNode
}

export function StaffFormDialog({
  businessId,
  editingStaff,
  availableUsers,
  children,
}: StaffFormDialogProps) {
  const [open, setOpen] = useState(false)
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<StaffFormData>({
    resolver: zodResolver(staffSchema),
    defaultValues: editingStaff
      ? {
          name: editingStaff.name,
          role: editingStaff.role,
          is_active: editingStaff.is_active,
          user_id: editingStaff.user_id,
        }
      : {
          name: "",
          role: "",
          is_active: true,
          user_id: null,
        },
  })

  const onSubmit = async (data: StaffFormData) => {
    setIsLoading(true)
    try {
      if (editingStaff) {
        const result = await updateStaffMember(editingStaff.id, businessId, {
          name: data.name,
          role: data.role,
          is_active: data.is_active,
          user_id: data.user_id || null,
        })

        if (result.success) {
          toast.success("Staff member updated successfully!")
          setOpen(false)
          form.reset()
        } else {
          toast.error(result.error || "Failed to update staff member")
        }
      } else {
        const result = await createStaffMember(businessId, {
          name: data.name,
          role: data.role,
          is_active: data.is_active,
          user_id: data.user_id || null,
        })

        if (result.success) {
          toast.success("Staff member created successfully!")
          setOpen(false)
          form.reset()
        } else {
          toast.error(result.error || "Failed to create staff member")
        }
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {children || (
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            Add Staff Member
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {editingStaff ? "Edit Staff Member" : "Add Staff Member"}
          </DialogTitle>
          <DialogDescription>
            {editingStaff
              ? "Update the staff member's information"
              : "Add a new staff member to your business"}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              placeholder="e.g., Luis Gómez"
              {...form.register("name")}
            />
            {form.formState.errors.name && (
              <p className="text-sm text-red-500">{form.formState.errors.name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">Role / Position</Label>
            <Input
              id="role"
              placeholder="e.g., Barber, Hairdresser, Stylist"
              {...form.register("role")}
            />
            {form.formState.errors.role && (
              <p className="text-sm text-red-500">{form.formState.errors.role.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="user_id">Link to System User (Optional)</Label>
            <Select
              value={form.watch("user_id") || "none"}
              onValueChange={(value) =>
                form.setValue("user_id", value === "none" ? null : value)
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a user (optional)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No user link</SelectItem>
                {availableUsers.map((user) => (
                  <SelectItem key={user.id} value={user.id}>
                    {user.full_name || user.email} ({user.email})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Link a user account to this staff member for account management
            </p>
          </div>

          <div className="flex items-center justify-between space-x-2">
            <Label htmlFor="is_active">Active</Label>
            <Switch
              id="is_active"
              checked={form.watch("is_active")}
              onCheckedChange={(checked) => form.setValue("is_active", checked)}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isLoading}>
              {isLoading
                ? editingStaff
                  ? "Updating..."
                  : "Creating..."
                : editingStaff
                ? "Update"
                : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
