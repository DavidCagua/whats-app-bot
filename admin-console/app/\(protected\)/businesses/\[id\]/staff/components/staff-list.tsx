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
import { Edit, Trash2, ToggleRight, ToggleLeft } from "lucide-react"
import { toast } from "sonner"
import { deleteStaffMember, updateStaffMember } from "@/lib/actions/staff"
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
import { StaffFormDialog } from "./staff-form"

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
  created_at: Date
}

interface StaffListProps {
  data: StaffMember[]
  businessId: string
  canEdit: boolean
  availableUsers: Array<{ id: string; email: string; full_name: string | null }>
}

export function StaffList({ data, businessId, canEdit, availableUsers }: StaffListProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)

  const handleDelete = async (staffId: string) => {
    setDeletingId(staffId)
    try {
      const result = await deleteStaffMember(staffId, businessId)
      if (result.success) {
        toast.success("Staff member deleted")
      } else {
        toast.error(result.error || "Failed to delete")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setDeletingId(null)
    }
  }

  const handleToggleActive = async (staff: StaffMember) => {
    setTogglingId(staff.id)
    try {
      const result = await updateStaffMember(staff.id, businessId, {
        is_active: !staff.is_active,
      })
      if (result.success) {
        toast.success(
          `Staff member ${result.staff.is_active ? "activated" : "deactivated"}`
        )
      } else {
        toast.error(result.error || "Failed to update")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setTogglingId(null)
    }
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Linked User</TableHead>
            <TableHead>Status</TableHead>
            {canEdit && <TableHead className="text-right">Actions</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.length === 0 ? (
            <TableRow>
              <TableCell colSpan={canEdit ? 5 : 4} className="text-center text-muted-foreground">
                No staff members yet
              </TableCell>
            </TableRow>
          ) : (
            data.map((staff) => (
              <TableRow key={staff.id}>
                <TableCell className="font-medium">{staff.name}</TableCell>
                <TableCell>
                  <Badge variant="secondary">{staff.role}</Badge>
                </TableCell>
                <TableCell>
                  {staff.user ? (
                    <div className="flex flex-col">
                      <span className="text-sm">{staff.user.full_name || "No name"}</span>
                      <span className="text-xs text-muted-foreground">{staff.user.email}</span>
                    </div>
                  ) : (
                    <span className="text-sm text-muted-foreground">Not linked</span>
                  )}
                </TableCell>
                <TableCell>
                  {staff.is_active ? (
                    <Badge variant="default" className="bg-green-500">Active</Badge>
                  ) : (
                    <Badge variant="secondary">Inactive</Badge>
                  )}
                </TableCell>

                {canEdit && (
                  <TableCell className="text-right space-x-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleToggleActive(staff)}
                      disabled={togglingId === staff.id}
                      title={staff.is_active ? "Deactivate" : "Activate"}
                    >
                      {staff.is_active ? (
                        <ToggleRight className="h-4 w-4" />
                      ) : (
                        <ToggleLeft className="h-4 w-4" />
                      )}
                    </Button>

                    <StaffFormDialog
                      businessId={businessId}
                      editingStaff={staff}
                      availableUsers={availableUsers}
                    >
                      <Button variant="ghost" size="icon">
                        <Edit className="h-4 w-4" />
                      </Button>
                    </StaffFormDialog>

                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="ghost" size="icon" disabled={deletingId === staff.id}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Delete Staff Member</AlertDialogTitle>
                          <AlertDialogDescription>
                            Are you sure you want to delete {staff.name}? This action cannot be undone.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() => handleDelete(staff.id)}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                          >
                            Delete
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
