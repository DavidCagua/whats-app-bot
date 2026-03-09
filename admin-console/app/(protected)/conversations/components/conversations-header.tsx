import { ConversationsAccess } from "@/lib/conversations-permissions"
import { MessageSquare, Users, TrendingUp } from "lucide-react"

type ConversationsHeaderProps = {
  role?: string
  access: ConversationsAccess
  stats?: {
    totalMessages: number
    uniqueCustomers: number
    todayMessages: number
  } | null
}

export function ConversationsHeader({ role, access, stats }: ConversationsHeaderProps) {
  // Determine title based on role
  const getTitle = () => {
    if (role === "super_admin") {
      return "Conversations"
    }

    if (access.businesses.length === 1) {
      return `Conversations - ${access.businesses[0].name}`
    }

    return "Conversations"
  }

  return (
    <div className="flex items-center justify-between">
      <div>
        <h1 className="text-3xl font-bold">{getTitle()}</h1>
      </div>

      {stats && (
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-3">
            <MessageSquare className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-xs text-muted-foreground">Total Messages</p>
              <p className="text-xl font-bold">{stats.totalMessages.toLocaleString()}</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Users className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-xs text-muted-foreground">Unique Customers</p>
              <p className="text-xl font-bold">{stats.uniqueCustomers.toLocaleString()}</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <TrendingUp className="h-5 w-5 text-muted-foreground" />
            <div>
              <p className="text-xs text-muted-foreground">Today</p>
              <p className="text-xl font-bold">{stats.todayMessages.toLocaleString()}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
