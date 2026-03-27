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
      title: "Conversaciones",
      value: uniqueThreads,
      description: `${messageCount} mensajes`,
      href: `/businesses/${id}/inbox`,
      icon: MessageSquare,
    },
    {
      title: "Próximas citas",
      value: upcomingBookings,
      description: "Desde hoy (no canceladas)",
      href: `/businesses/${id}/bookings`,
      icon: CalendarDays,
    },
    {
      title: "Productos",
      value: productCount,
      description: "En catálogo",
      href: `/businesses/${id}/products`,
      icon: Package,
    },
    {
      title: "Pedidos",
      value: orderCount,
      description: "Total pedidos",
      href: `/businesses/${id}/orders`,
      icon: ShoppingCart,
    },
    {
      title: "Personal",
      value: staffActiveCount,
      description: "Activos (calendario)",
      href: `/businesses/${id}/staff`,
      icon: Users,
    },
    {
      title: "Acceso",
      value: accessCount,
      description: "Usuarios de la consola",
      href: `/businesses/${id}/team`,
      icon: UserCog,
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Resumen</h1>
        <p className="text-sm text-muted-foreground">
          {business.name} — estadísticas rápidas solo de este negocio
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
