"use client"

import { useState } from "react"
import { UserEditForm } from "./user-edit-form"
import { UserBusinessAssignments } from "./user-business-assignments"

interface UserEditPageContentProps {
  user: {
    id: string
    email: string
    full_name: string
    role: string | null
    is_active: boolean
  }
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

export function UserEditPageContent({
  user,
  userBusinesses,
  availableBusinesses,
}: UserEditPageContentProps) {
  // Track the current role selection to dynamically show/hide business assignments
  const [currentRole, setCurrentRole] = useState(user.role || "business_user")
  const isBusinessUser = currentRole !== "super_admin"

  return (
    <div className={`grid gap-6 ${isBusinessUser ? "lg:grid-cols-2" : ""}`}>
      <UserEditForm user={user} onRoleChange={setCurrentRole} />
      {isBusinessUser && (
        <UserBusinessAssignments
          userId={user.id}
          userBusinesses={userBusinesses}
          availableBusinesses={availableBusinesses}
        />
      )}
    </div>
  )
}
