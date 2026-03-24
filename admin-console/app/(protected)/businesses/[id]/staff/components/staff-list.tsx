"use client"

import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Pencil, Trash2, ToggleLeft, ToggleRight } from "lucide-react"
import { toast } from "sonner"
import { deleteStaffMember, toggleStaffActive } from "@/lib/actions/staff"
import { StaffFormDialog } from "./staff-form-dialog"
import { StaffMember } from "@/types/staff"

interface StaffListProps {
  businessId: string
  staffMembers: StaffMember[]
  activeCount: number
  inactiveCount: number
}

export function StaffList({
  businessId,
  staffMembers,
  activeCount,
  inactiveCount,
}: StaffListProps) {
  const [members, setMembers] = useState<StaffMember[]>(staffMembers)
  const [editingMember, setEditingMember] = useState<StaffMember | null>(null)
  const [isDeleting, setIsDeleting] = useState<string | null>(null)

  const handleDelete = async (memberId: string) => {
    setIsDeleting(memberId)
    try {
      const result = await deleteStaffMember(memberId)
      if (result.success) {
        setMembers((prev) => prev.filter((m) => m.id !== memberId))
        toast.success("Staff member deleted")
      } else {
        toast.error(result.error || "Failed to delete")
      }
    } finally {
      setIsDeleting(null)
    }
  }

  const handleToggle = async (memberId: string, currentState: boolean) => {
    try {
      const result = await toggleStaffActive(memberId, !currentState)
      if (result.success) {
        setMembers((prev) =>
          prev.map((m) =>
            m.id === memberId ? { ...m, is_active: !currentState } : m
          )
        )
        toast.success(
          `Staff member ${!currentState ? "activated" : "deactivated"}`
        )
      } else {
        toast.error(result.error || "Failed to update")
      }
    } catch (error) {
      toast.error("An error occurred")
    }
  }

  const activeStaff = members.filter((s) => s.is_active)
  const inactiveStaff = members.filter((s) => !s.is_active)

  const StaffTable = ({ staff }: { staff: StaffMember[] }) => (
    <div className="rounded-lg border overflow-hidden">
      <table className="w-full">
        <thead className="bg-muted">
          <tr>
            <th className="px-6 py-3 text-left text-sm font-medium">Name</th>
            <th className="px-6 py-3 text-left text-sm font-medium">Role</th>
            <th className="px-6 py-3 text-left text-sm font-medium">
              Linked User
            </th>
            <th className="px-6 py-3 text-right text-sm font-medium">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {staff.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-6 py-8 text-center text-muted-foreground">
                No staff members
              </td>
            </tr>
          ) : (
            staff.map((member) => (
              <tr key={member.id} className="hover:bg-muted/50">
                <td className="px-6 py-4">
                  <div className="font-medium">{member.name}</div>
                </td>
                <td className="px-6 py-4">
                  <Badge variant="outline">{member.role}</Badge>
                </td>
                <td className="px-6 py-4">
                  {member.user ? (
                    <div className="text-sm">
                      <div className="font-medium">{member.user.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {member.user.email}
                      </div>
                    </div>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      Not linked
                    </span>
                  )}
                </td>
                <td className="px-6 py-4">
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditingMember(member)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleToggle(member.id, member.is_active ?? true)}
                    >
                      {member.is_active ? (
                        <ToggleRight className="h-4 w-4" />
                      ) : (
                        <ToggleLeft className="h-4 w-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(member.id)}
                      disabled={isDeleting === member.id}
                    >
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )

  return (
    <>
      <Tabs defaultValue="all" className="space-y-4">
        <TabsList>
          <TabsTrigger value="all">
            All ({members.length})
          </TabsTrigger>
          <TabsTrigger value="active">Active ({activeCount})</TabsTrigger>
          <TabsTrigger value="inactive">Inactive ({inactiveCount})</TabsTrigger>
        </TabsList>

        <TabsContent value="all" className="space-y-4">
          <StaffTable staff={members} />
        </TabsContent>

        <TabsContent value="active" className="space-y-4">
          <StaffTable staff={activeStaff} />
        </TabsContent>

        <TabsContent value="inactive" className="space-y-4">
          <StaffTable staff={inactiveStaff} />
        </TabsContent>
      </Tabs>

      {editingMember && (
        <StaffFormDialog
          businessId={businessId}
          staff={editingMember}
          onClose={() => setEditingMember(null)}
          onSave={(updated) => {
            setMembers((prev) =>
              prev.map((m) => (m.id === updated.id ? updated : m))
            )
            setEditingMember(null)
          }}
        />
      )}
    </>
  )
}
