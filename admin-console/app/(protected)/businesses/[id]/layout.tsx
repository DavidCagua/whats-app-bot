import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { BusinessNav } from "./components/business-nav"
import Link from "next/link"
import { ChevronLeft } from "lucide-react"

interface BusinessLayoutProps {
  children: React.ReactNode
  params: Promise<{ id: string }>
}

export default async function BusinessLayout({ children, params }: BusinessLayoutProps) {
  const { id } = await params
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({
    where: { id },
  })

  if (!business) {
    notFound()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Link
          href="/businesses"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-4 w-4" />
          Businesses
        </Link>
      </div>

      <div>
        <h1 className="text-2xl font-bold">{business.name}</h1>
        <p className="text-sm text-muted-foreground capitalize">
          {business.business_type || "Business"}
        </p>
      </div>

      <BusinessNav businessId={id} />

      {children}
    </div>
  )
}
