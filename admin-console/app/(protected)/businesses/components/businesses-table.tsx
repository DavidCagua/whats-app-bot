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
import { Settings, Users } from "lucide-react"
import Link from "next/link"
import { format } from "date-fns"

type Business = {
  id: string
  name: string
  business_type: string | null
  is_active: boolean | null
  created_at: Date | null
  whatsapp_numbers: Array<{
    id: string
    phone_number: string
    is_active: boolean | null
  }>
}

interface BusinessesTableProps {
  data: Business[]
}

export function BusinessesTable({ data }: BusinessesTableProps) {
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>WhatsApp Numbers</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-muted-foreground">
                No businesses found. Create your first one to get started.
              </TableCell>
            </TableRow>
          ) : (
            data.map((business) => (
              <TableRow key={business.id}>
                <TableCell className="font-medium">{business.name}</TableCell>
                <TableCell className="capitalize">
                  {business.business_type || "N/A"}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    {business.whatsapp_numbers.length === 0 ? (
                      <span className="text-sm text-muted-foreground">
                        No numbers
                      </span>
                    ) : (
                      business.whatsapp_numbers.map((number) => (
                        <div key={number.id} className="flex items-center gap-2">
                          <span className="text-sm">{number.phone_number}</span>
                          {number.is_active && (
                            <Badge variant="secondary" className="h-5 text-xs">
                              Active
                            </Badge>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  {business.is_active ? (
                    <Badge variant="default">Active</Badge>
                  ) : (
                    <Badge variant="secondary">Inactive</Badge>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {business.created_at
                    ? format(new Date(business.created_at), "MMM d, yyyy")
                    : "N/A"}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/businesses/${business.id}/team`}>
                        <Users className="mr-2 h-4 w-4" />
                        Team
                      </Link>
                    </Button>
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/businesses/${business.id}/settings`}>
                        <Settings className="mr-2 h-4 w-4" />
                        Settings
                      </Link>
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
