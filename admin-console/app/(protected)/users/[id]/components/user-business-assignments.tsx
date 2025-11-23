"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { Building2, Plus, X } from "lucide-react"
import { toast } from "sonner"
import { assignUserToBusiness, removeUserFromBusiness } from "@/lib/actions/users"

interface UserBusinessAssignmentsProps {
  userId: string
  userBusinesses: Array<{
    id: string
    name: string
    role: string
  }>
  availableBusinesses: Array<{
    id: string
    name: string
  }>
}

export function UserBusinessAssignments({
  userId,
  userBusinesses,
  availableBusinesses,
}: UserBusinessAssignmentsProps) {
  const [isAdding, setIsAdding] = useState(false)
  const [selectedBusiness, setSelectedBusiness] = useState("")
  const [selectedRole, setSelectedRole] = useState("staff")
  const [removingId, setRemovingId] = useState<string | null>(null)

  // Filter out businesses already assigned
  const unassignedBusinesses = availableBusinesses.filter(
    (b) => !userBusinesses.some((ub) => ub.id === b.id)
  )

  const handleAdd = async () => {
    if (!selectedBusiness) {
      toast.error("Please select a business")
      return
    }

    setIsAdding(true)
    try {
      const result = await assignUserToBusiness(userId, selectedBusiness, selectedRole)
      if (result.success) {
        toast.success("Business assigned successfully")
        setSelectedBusiness("")
        setSelectedRole("staff")
      } else {
        toast.error(result.error || "Failed to assign business")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setIsAdding(false)
    }
  }

  const handleRemove = async (businessId: string) => {
    setRemovingId(businessId)
    try {
      const result = await removeUserFromBusiness(userId, businessId)
      if (result.success) {
        toast.success("Business assignment removed")
      } else {
        toast.error(result.error || "Failed to remove assignment")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Building2 className="h-5 w-5" />
          Business Assignments
        </CardTitle>
        <CardDescription>
          Manage which businesses this user can access
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Current assignments */}
        <div className="space-y-2">
          {userBusinesses.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No business assignments yet
            </p>
          ) : (
            userBusinesses.map((business) => (
              <div
                key={business.id}
                className="flex items-center justify-between rounded-lg border p-3"
              >
                <div className="flex items-center gap-3">
                  <span className="font-medium">{business.name}</span>
                  <Badge variant={business.role === "admin" ? "default" : "secondary"}>
                    {business.role}
                  </Badge>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleRemove(business.id)}
                  disabled={removingId === business.id}
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
            <p className="text-sm font-medium">Add Business Assignment</p>
            <div className="flex gap-2">
              <Select value={selectedBusiness} onValueChange={setSelectedBusiness}>
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder="Select business" />
                </SelectTrigger>
                <SelectContent>
                  {unassignedBusinesses.map((business) => (
                    <SelectItem key={business.id} value={business.id}>
                      {business.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={selectedRole} onValueChange={setSelectedRole}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="Role" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="staff">Staff</SelectItem>
                </SelectContent>
              </Select>

              <Button onClick={handleAdd} disabled={isAdding || !selectedBusiness}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
