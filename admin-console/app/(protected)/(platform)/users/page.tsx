import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { redirect } from "next/navigation"
import { getUsers } from "@/lib/actions/users"
import { Button } from "@/components/ui/button"
import { Plus } from "lucide-react"
import Link from "next/link"
import { UsersTable } from "./components/users-table"

export default async function UsersPage() {
  const session = await auth()

  if (!isSuperAdmin(session)) {
    redirect("/")
  }

  const users = await getUsers()

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Users</h1>
          <p className="text-muted-foreground">
            Manage system users and their business assignments
          </p>
        </div>
        <Button asChild>
          <Link href="/users/new">
            <Plus className="mr-2 h-4 w-4" />
            Add User
          </Link>
        </Button>
      </div>

      <UsersTable data={users} />
    </div>
  )
}
