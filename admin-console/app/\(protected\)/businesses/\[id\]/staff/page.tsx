import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness, isSuperAdmin } from "@/lib/permissions"
import { notFound } from "next/navigation"
import { StaffList } from "./components/staff-list"
import { StaffFormDialog } from "./components/staff-form"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Users } from "lucide-react"

interface StaffPageProps {
  params: {
    id: string
  }
}

export default async function StaffPage({ params }: StaffPageProps) {
  const { id: businessId } = await params
  const session = await auth()

  // Check access
  if (
    !session?.user ||
    (!isSuperAdmin(session) && !canAccessBusiness(session, businessId))
  ) {
    notFound()
  }

  const canEdit = isSuperAdmin(session) || canEditBusiness(session, businessId)

  // Fetch business
  const business = await prisma.businesses.findUnique({
    where: { id: businessId },
  })

  if (!business) {
    notFound()
  }

  // Fetch staff members
  const staffMembers = await prisma.staff_members.findMany({
    where: { business_id: businessId },
    include: {
      users: {
        select: {
          id: true,
          email: true,
          full_name: true,
        },
      },
    },
    orderBy: { name: "asc" },
  })

  // Fetch available users for linking
  const availableUsers = await prisma.users.findMany({
    where: {
      user_businesses: {
        some: {
          business_id: businessId,
        },
      },
    },
    select: {
      id: true,
      email: true,
      full_name: true,
    },
    orderBy: { full_name: "asc" },
  })

  const formattedStaff = staffMembers.map((s) => ({
    id: s.id,
    name: s.name,
    role: s.role,
    is_active: s.is_active,
    user_id: s.user_id,
    user: s.users
      ? {
          id: s.users.id,
          email: s.users.email,
          full_name: s.users.full_name,
        }
      : null,
    created_at: s.created_at,
  }))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Staff Members</h1>
          <p className="text-muted-foreground">
            Manage service providers and staff for {business.name}
          </p>
        </div>
        {canEdit && (
          <StaffFormDialog businessId={businessId} availableUsers={availableUsers} />
        )}
      </div>

      <Tabs defaultValue="all" className="w-full">
        <TabsList>
          <TabsTrigger value="all">
            All ({formattedStaff.length})
          </TabsTrigger>
          <TabsTrigger value="active">
            Active ({formattedStaff.filter((s) => s.is_active).length})
          </TabsTrigger>
          <TabsTrigger value="inactive">
            Inactive ({formattedStaff.filter((s) => !s.is_active).length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="all" className="mt-6">
          <StaffList
            data={formattedStaff}
            businessId={businessId}
            canEdit={canEdit}
            availableUsers={availableUsers}
          />
        </TabsContent>

        <TabsContent value="active" className="mt-6">
          <StaffList
            data={formattedStaff.filter((s) => s.is_active)}
            businessId={businessId}
            canEdit={canEdit}
            availableUsers={availableUsers}
          />
        </TabsContent>

        <TabsContent value="inactive" className="mt-6">
          <StaffList
            data={formattedStaff.filter((s) => !s.is_active)}
            businessId={businessId}
            canEdit={canEdit}
            availableUsers={availableUsers}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
