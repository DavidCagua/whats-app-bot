import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { redirect } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Package, ShoppingCart, Users } from "lucide-react"
import Link from "next/link"

interface BusinessOverviewPageProps {
  params: Promise<{ id: string }>
}

export default async function BusinessOverviewPage({ params }: BusinessOverviewPageProps) {
  const { id } = await params
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const [business, productCount, orderCount, teamCount] = await Promise.all([
    prisma.businesses.findUniqueOrThrow({ where: { id } }),
    prisma.products.count({ where: { business_id: id } }),
    prisma.orders.count({ where: { business_id: id } }),
    prisma.user_businesses.count({ where: { business_id: id } }),
  ])

  const cards = [
    {
      title: "Products",
      value: productCount,
      description: "Products in catalog",
      href: `/businesses/${id}/products`,
      icon: Package,
    },
    {
      title: "Orders",
      value: orderCount,
      description: "Total orders",
      href: `/businesses/${id}/orders`,
      icon: ShoppingCart,
    },
    {
      title: "Team Members",
      value: teamCount,
      description: "People with access",
      href: `/businesses/${id}/team`,
      icon: Users,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Overview</h2>
        <p className="text-sm text-muted-foreground">
          Quick stats for {business.name}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {cards.map(({ title, value, description, href, icon: Icon }) => (
          <Link key={title} href={href}>
            <Card className="transition-colors hover:bg-muted/50">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium">{title}</CardTitle>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{value}</div>
                <p className="text-xs text-muted-foreground">{description}</p>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
