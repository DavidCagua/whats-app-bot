import { ConversationsAccess } from "@/lib/conversations-permissions";
import { MessageSquare, Users, TrendingUp } from "lucide-react";

type ConversationsHeaderProps = {
  role?: string;
  access: ConversationsAccess;
  stats?: {
    totalMessages: number;
    uniqueCustomers: number;
    todayMessages: number;
  } | null;
};

export function ConversationsHeader({
  role,
  access,
  stats,
}: ConversationsHeaderProps) {
  const getTitle = () => {
    if (role === "super_admin") return "Conversaciones";
    if (access.businesses.length === 1)
      return `Conversaciones — ${access.businesses[0].name}`;
    return "Conversaciones";
  };

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
        {getTitle()}
      </h1>

      {stats && (
        <div className="flex items-center gap-4 sm:gap-8 flex-wrap">
          <div className="flex items-center gap-2 sm:gap-3">
            <MessageSquare className="h-4 w-4 sm:h-5 sm:w-5 text-muted-foreground flex-shrink-0" />
            <div>
              <p className="text-xs text-muted-foreground leading-none">
                Total mensajes
              </p>
              <p className="text-lg sm:text-xl font-bold leading-tight">
                {stats.totalMessages.toLocaleString()}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <Users className="h-4 w-4 sm:h-5 sm:w-5 text-muted-foreground flex-shrink-0" />
            <div>
              <p className="text-xs text-muted-foreground leading-none">
                Clientes
              </p>
              <p className="text-lg sm:text-xl font-bold leading-tight">
                {stats.uniqueCustomers.toLocaleString()}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <TrendingUp className="h-4 w-4 sm:h-5 sm:w-5 text-muted-foreground flex-shrink-0" />
            <div>
              <p className="text-xs text-muted-foreground leading-none">Hoy</p>
              <p className="text-lg sm:text-xl font-bold leading-tight">
                {stats.todayMessages.toLocaleString()}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
