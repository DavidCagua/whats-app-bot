import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { prisma } from "@/lib/prisma"
import { Building2, Hash, MessageSquare, Users } from "lucide-react"

export default async function DashboardPage() {
  // Fetch stats
  const [businessCount, whatsappCount, conversationCount, userCount] = await Promise.all([
    prisma.businesses.count(),
    prisma.whatsapp_numbers.count(),
    prisma.conversations.count(),
    prisma.users.count(),
  ])

  const stats = [
    {
      title: "Total Businesses",
      value: businessCount,
      icon: Building2,
      description: "Active businesses",
    },
    {
      title: "WhatsApp Numbers",
      value: whatsappCount,
      icon: Hash,
      description: "Connected numbers",
    },
    {
      title: "Conversations",
      value: conversationCount,
      icon: MessageSquare,
      description: "Total messages",
    },
    {
      title: "Users",
      value: userCount,
      icon: Users,
      description: "System users",
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Dashboard</h1>
        <p className="text-muted-foreground">
          Overview of your multi-tenant WhatsApp bot system
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <Card key={stat.title}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">{stat.title}</CardTitle>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stat.value}</div>
                <p className="text-xs text-muted-foreground">{stat.description}</p>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
