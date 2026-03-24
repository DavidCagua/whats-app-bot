"use client"

import { useState, useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { toast } from "sonner"
import { createStaffMember, updateStaffMember } from "@/lib/actions/staff"
import { getBusinessUsers } from "@/lib/actions/users"
import { StaffMember } from "@/types/staff"

const staffFormSchema = z.object({
  name: z.string().min(1, "Name is required"),
  role: z.string().min(1, "Role is required"),
  is_active: z.boolean(),
  user_id: z.string().optional().nullable(),
})

type StaffFormData = z.infer<typeof staffFormSchema>

interface StaffFormDialogProps {
  businessId: string
  staff?: StaffMember
  onClose: () => void
  onSave: (staff: StaffMember) => void
}

export function StaffFormDialog({
  businessId,
  staff,
  onClose,
  onSave,
}: StaffFormDialogProps) {
  const [isLoading, setIsLoading] = useState(false)
  const [users, setUsers] = useState<Array<{ id: string; email: string; name: string | null }>>([])
  const [usersLoading, setUsersLoading] = useState(false)

  const form = useForm<StaffFormData>({
    resolver: zodResolver(staffFormSchema),
    defaultValues: {
      name: staff?.name || "",
      role: staff?.role || "",
      is_active: staff?.is_active ?? true,
      user_id: staff?.user_id || null,
    },
  })

  useEffect(() => {
    const loadUsers = async () => {
      setUsersLoading(true)
      try {
        const result = await getBusinessUsers(businessId)
        if (Array.isArray(result)) {
          setUsers(result)
        } else if (result.success && result.users) {
          setUsers(result.users)
        }
      } catch (error) {
        console.error("Failed to load users:", error)
      } finally {
        setUsersLoading(false)
      }
    }
    loadUsers()
  }, [businessId])

  const onSubmit = async (data: StaffFormData) => {
    setIsLoading(true)
    try {
      const result = staff
        ? await updateStaffMember(staff.id, {
            ...data,
            user_id: data.user_id || null,
          })
        : await createStaffMember(businessId, {
            ...data,
            user_id: data.user_id || null,
          })

      if (result.success && result.staff) {
        toast.success(staff ? "Staff member updated" : "Staff member created")
        onSave(result.staff)
      } else {
        toast.error(result.error || "Failed to save")
      }
    } catch (error) {
      toast.error("An error occurred")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          {...form.register("name")}
          placeholder="e.g., Luis Gómez"
        />
        {form.formState.errors.name && (
          <p className="text-sm text-red-500">
            {form.formState.errors.name.message}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="role">Role</Label>
        <Input
          id="role"
          {...form.register("role")}
          placeholder="e.g., Barbero, Estilista"
        />
        {form.formState.errors.role && (
          <p className="text-sm text-red-500">
            {form.formState.errors.role.message}
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="user">Link to User (Optional)</Label>
        <Select
          value={form.watch("user_id") || ""}
          onValueChange={(value) => form.setValue("user_id", value || "")}
        >
          <SelectTrigger id="user">
            <SelectValue placeholder="No user linked" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">No user</SelectItem>
            {usersLoading ? (
              <div className="px-2 py-1 text-sm text-muted-foreground">
                Loading...
              </div>
            ) : (
              users.map((user) => (
                <SelectItem key={user.id} value={user.id}>
                  {user.name || user.email}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center justify-between rounded-lg border p-3">
        <Label htmlFor="is_active" className="cursor-pointer">
          Active
        </Label>
        <Switch
          id="is_active"
          checked={form.watch("is_active")}
          onCheckedChange={(checked) => form.setValue("is_active", checked)}
        />
      </div>

      <div className="flex justify-end gap-2 pt-4">
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={isLoading}>
          {isLoading ? "Saving..." : staff ? "Update" : "Create"}
        </Button>
      </div>
    </form>
  )
}
