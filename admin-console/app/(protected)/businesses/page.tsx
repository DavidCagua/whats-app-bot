import { prisma } from "@/lib/prisma"
import { BusinessesTable } from "./components/businesses-table"
import { Button } from "@/components/ui/button"
import { Plus } from "lucide-react"
import Link from "next/link"

export default async function BusinessesPage() {
  const businesses = await prisma.businesses.findMany({
    include: {
      whatsapp_numbers: true,
    },
    orderBy: {
      created_at: "desc",
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Businesses</h1>
          <p className="text-muted-foreground">
            Manage businesses and their WhatsApp configurations
          </p>
        </div>
        <Button asChild>
          <Link href="/businesses/new">
            <Plus className="mr-2 h-4 w-4" />
            Add Business
          </Link>
        </Button>
      </div>

      <BusinessesTable data={businesses} />
    </div>
  )
}
