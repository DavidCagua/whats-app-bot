import { redirect } from "next/navigation"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { prisma } from "@/lib/prisma"
import { StaffList } from "./components/staff-list"
import { Button } from "@/components/ui/button"
import { Plus } from "lucide-react"
import { StaffForm } from "./components/staff-form"

interface StaffPageProps {
  params: {
    id: string
  }
}

export const metadata = {
  title: "Staff Members",
}

export default async function StaffPage({ params }: StaffPageProps) {
  const session = await auth()

  if (!session?.user) {
    redirect("/login")
  }

  if (!canAccessBusiness(session, params.id)) {
    redirect("/")
  }

  // Verify business exists
  const business = await prisma.businesses.findUnique({
    where: { id: params.id },
  })

  if (!business) {
    redirect("/")
  }

  // Get all staff members
  const staffMembers = await prisma.staff_members.findMany({
    where: { business_id: params.id },
    include: {
      users: {
        select: {
          id: true,
          email: true,
          full_name: true,
        },
      },
    },
    orderBy: { created_at: "desc" },
  })

  const activeStaff = staffMembers.filter((s) => s.is_active)
  const inactiveStaff = staffMembers.filter((s) => !s.is_active)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Staff Members</h1>
          <p className="text-muted-foreground mt-2">
            Manage your team members and their roles
          </p>
        </div>
        <StaffForm businessId={params.id} />
      </div>

      <StaffList
        businessId={params.id}
        staffMembers={staffMembers}
        activeCount={activeStaff.length}
        inactiveCount={inactiveStaff.length}
      />
    </div>
  )
}
