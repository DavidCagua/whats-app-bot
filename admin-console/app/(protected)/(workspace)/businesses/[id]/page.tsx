import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness } from "@/lib/permissions"
import { redirect } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  CalendarDays,
  MessageSquare,
  Package,
  ShoppingCart,
  UserCog,
  Users,
} from "lucide-react"
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

  const now = new Date()
  const startOfToday = new Date(now)
  startOfToday.setHours(0, 0, 0, 0)

  const [
    business,
    productCount,
    orderCount,
    accessCount,
    staffActiveCount,
    messageCount,
    uniqueThreads,
    upcomingBookings,
  ] = await Promise.all([
    prisma.businesses.findUniqueOrThrow({ where: { id } }),
    prisma.products.count({ where: { business_id: id } }),
    prisma.orders.count({ where: { business_id: id } }),
    prisma.user_businesses.count({ where: { business_id: id } }),
    prisma.staff_members.count({ where: { business_id: id, is_active: true } }),
    prisma.conversations.count({ where: { business_id: id } }),
    prisma.conversations
      .groupBy({
        by: ["whatsapp_id"],
        where: { business_id: id },
      })
      .then((rows) => rows.length),
    prisma.bookings.count({
      where: {
        business_id: id,
        status: { not: "cancelled" },
        start_at: { gte: startOfToday },
      },
    }),
  ])

  const cards = [
    {
      title: "Inbox threads",
      value: uniqueThreads,
      description: `${messageCount} messages`,
      href: `/businesses/${id}/inbox`,
      icon: MessageSquare,
    },
    {
      title: "Bookings ahead",
      value: upcomingBookings,
      description: "From today (not cancelled)",
      href: `/businesses/${id}/bookings`,
      icon: CalendarDays,
    },
    {
      title: "Products",
      value: productCount,
      description: "In catalog",
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
      title: "Staff",
      value: staffActiveCount,
      description: "Active (calendar)",
      href: `/businesses/${id}/staff`,
      icon: Users,
    },
    {
      title: "Access",
      value: accessCount,
      description: "Console users",
      href: `/businesses/${id}/team`,
      icon: UserCog,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
        <p className="text-sm text-muted-foreground">
          {business.name} — quick stats for this business only
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map(({ title, value, description, href, icon: Icon }) => (
          <Link key={title} href={href}>
            <Card className="transition-colors hover:bg-muted/50 h-full">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
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
