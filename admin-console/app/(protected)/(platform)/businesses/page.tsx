import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { isSuperAdmin, getAccessibleBusinessIds } from "@/lib/permissions"
import { BusinessesTable } from "./components/businesses-table"
import { Button } from "@/components/ui/button"
import { Plus } from "lucide-react"
import Link from "next/link"

export default async function BusinessesPage() {
  const session = await auth()

  // Build query filter based on user permissions
  const businessIds = getAccessibleBusinessIds(session)
  const whereClause = isSuperAdmin(session)
    ? {} // Super admins see all businesses
    : { id: { in: businessIds } } // Business users see only their businesses

  const businesses = await prisma.businesses.findMany({
    where: whereClause,
    include: {
      whatsapp_numbers: true,
    },
    orderBy: {
      created_at: "desc",
    },
  })

  const canAddBusiness = isSuperAdmin(session)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Businesses</h1>
          <p className="text-muted-foreground">
            {isSuperAdmin(session)
              ? "Manage all businesses and their WhatsApp configurations"
              : "View your business configurations"}
          </p>
        </div>
        {canAddBusiness && (
          <Button asChild>
            <Link href="/businesses/new">
              <Plus className="mr-2 h-4 w-4" />
              Add Business
            </Link>
          </Button>
        )}
      </div>

      <BusinessesTable data={businesses} />
    </div>
  )
}
