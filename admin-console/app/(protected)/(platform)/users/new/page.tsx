import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin } from "@/lib/permissions"
import { redirect } from "next/navigation"
import { CreateUserForm } from "./components/create-user-form"

export default async function NewUserPage() {
  const session = await auth()

  if (!isSuperAdmin(session)) {
    redirect("/")
  }

  const businesses = await prisma.businesses.findMany({
    orderBy: { name: "asc" },
  })

  const availableBusinesses = businesses.map((b) => ({
    id: b.id,
    name: b.name,
  }))

  return (
    <CreateUserForm availableBusinesses={availableBusinesses} />
  )
}
