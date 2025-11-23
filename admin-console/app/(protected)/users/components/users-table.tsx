"use client"

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
import { Settings, Shield, User } from "lucide-react"
import Link from "next/link"

interface UserData {
  id: string
  email: string
  full_name: string | null
  role: string | null
  is_active: boolean | null
  created_at: Date | null
  businesses: Array<{
    id: string
    name: string
    role: string | null
  }>
}

interface UsersTableProps {
  data: UserData[]
}

export function UsersTable({ data }: UsersTableProps) {
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>User</TableHead>
            <TableHead>System Role</TableHead>
            <TableHead>Businesses</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-muted-foreground">
                No users found
              </TableCell>
            </TableRow>
          ) : (
            data.map((user) => (
              <TableRow key={user.id}>
                <TableCell>
                  <div className="flex flex-col">
                    <span className="font-medium">{user.full_name || "No name"}</span>
                    <span className="text-sm text-muted-foreground">{user.email}</span>
                  </div>
                </TableCell>
                <TableCell>
                  {user.role === "super_admin" ? (
                    <Badge variant="default" className="gap-1">
                      <Shield className="h-3 w-3" />
                      Super Admin
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="gap-1">
                      <User className="h-3 w-3" />
                      Business User
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  {user.businesses.length === 0 ? (
                    <span className="text-muted-foreground text-sm">No businesses</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {user.businesses.map((business) => (
                        <Badge key={business.id} variant="outline" className="text-xs">
                          {business.name}
                          <span className="ml-1 text-muted-foreground">
                            ({business.role || "staff"})
                          </span>
                        </Badge>
                      ))}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  {user.is_active ? (
                    <Badge variant="default" className="bg-green-500">Active</Badge>
                  ) : (
                    <Badge variant="secondary">Inactive</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" asChild>
                    <Link href={`/users/${user.id}`}>
                      <Settings className="h-4 w-4" />
                    </Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
