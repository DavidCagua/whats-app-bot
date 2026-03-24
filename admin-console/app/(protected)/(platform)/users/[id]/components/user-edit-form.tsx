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
import { Switch } from "@/components/ui/switch"
import { User, Save, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { updateUser, deleteUser } from "@/lib/actions/users"
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

const userEditSchema = z.object({
  email: z.string().email("Invalid email address"),
  full_name: z.string().min(1, "Full name is required"),
  role: z.string(),
  is_active: z.boolean(),
  password: z.string().optional(),
})

type UserEditFormData = z.infer<typeof userEditSchema>

interface UserEditFormProps {
  user: {
    id: string
    email: string
    full_name: string
    role: string | null
    is_active: boolean
  }
  onRoleChange?: (role: string) => void
}

export function UserEditForm({ user, onRoleChange }: UserEditFormProps) {
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  const form = useForm<UserEditFormData>({
    resolver: zodResolver(userEditSchema),
    defaultValues: {
      email: user.email,
      full_name: user.full_name,
      role: user.role || "business_user",
      is_active: user.is_active,
      password: "",
    },
  })

  const onSubmit = async (data: UserEditFormData) => {
    setIsLoading(true)
    try {
      const result = await updateUser(user.id, {
        email: data.email,
        full_name: data.full_name,
        role: data.role === "super_admin" ? "super_admin" : null,
        is_active: data.is_active,
        password: data.password || undefined,
      })

      if (result.success) {
        toast.success("User updated successfully!")
      } else {
        toast.error(result.error || "Failed to update user")
      }
    } catch {
      toast.error("An error occurred while updating the user")
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async () => {
    setIsDeleting(true)
    try {
      const result = await deleteUser(user.id)
      if (result.success) {
        toast.success("User deleted successfully")
        router.push("/users")
      } else {
        toast.error(result.error || "Failed to delete user")
      }
    } catch {
      toast.error("An error occurred while deleting the user")
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <User className="h-5 w-5" />
          User Details
        </CardTitle>
        <CardDescription>
          Update user information and system role
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="full_name">Full Name</Label>
            <Input
              id="full_name"
              {...form.register("full_name")}
            />
            {form.formState.errors.full_name && (
              <p className="text-sm text-red-500">{form.formState.errors.full_name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              {...form.register("email")}
            />
            {form.formState.errors.email && (
              <p className="text-sm text-red-500">{form.formState.errors.email.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">New Password (leave blank to keep current)</Label>
            <Input
              id="password"
              type="password"
              {...form.register("password")}
              placeholder="Enter new password"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">System Role</Label>
            <Select
              value={form.watch("role")}
              onValueChange={(value) => {
                form.setValue("role", value)
                onRoleChange?.(value)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select role" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="business_user">Business User</SelectItem>
                <SelectItem value="super_admin">Super Admin (OmnIA Team)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between rounded-lg border p-4">
            <div className="space-y-0.5">
              <Label htmlFor="is_active">Active Status</Label>
              <p className="text-sm text-muted-foreground">
                Inactive users cannot log in
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
                  Delete User
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete User?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This will permanently delete <strong>{user.full_name || user.email}</strong> and remove all their business assignments.
                    This action cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleDelete}
                    disabled={isDeleting}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    {isDeleting ? "Deleting..." : "Delete User"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>

            <Button type="submit" disabled={isLoading}>
              <Save className="mr-2 h-4 w-4" />
              {isLoading ? "Saving..." : "Save Changes"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
