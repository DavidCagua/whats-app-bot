import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { UserEditPageContent } from "./components/user-edit-page-content"

interface UserPageProps {
  params: {
    id: string
  }
}

export default async function UserPage({ params }: UserPageProps) {
  const { id } = await params
  const session = await auth()

  if (!isSuperAdmin(session)) {
    redirect("/")
  }

  const user = await prisma.users.findUnique({
    where: { id },
    include: {
      user_businesses: {
        include: {
          businesses: true,
        },
      },
    },
  })

  if (!user) {
    notFound()
  }

  const allBusinesses = await prisma.businesses.findMany({
    orderBy: { name: "asc" },
  })

  const userData = {
    id: user.id,
    email: user.email,
    full_name: user.full_name || "",
    role: user.role,
    is_active: user.is_active ?? true,
  }

  const userBusinesses = user.user_businesses.map((ub) => ({
    id: ub.business_id,
    name: ub.businesses.name,
    role: ub.role || "staff",
  }))

  const availableBusinesses = allBusinesses.map((b) => ({
    id: b.id,
    name: b.name,
  }))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Edit User</h1>
        <p className="text-muted-foreground">
          Manage user details and access for {user.full_name || user.email}
        </p>
      </div>

      <UserEditPageContent
        user={userData}
        userBusinesses={userBusinesses}
        availableBusinesses={availableBusinesses}
      />
    </div>
  )
}
