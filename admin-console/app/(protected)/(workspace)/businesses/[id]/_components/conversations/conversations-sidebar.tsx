"use client";

import { useEffect, useState, useTransition } from "react";

const LAST_SEEN_PREFIX = "inbox:lastSeen:";

function loadLastSeen(): Record<string, number> {
  const result: Record<string, number> = {};
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(LAST_SEEN_PREFIX)) {
      result[key.slice(LAST_SEEN_PREFIX.length)] = Number(
        localStorage.getItem(key),
      );
    }
  }
  return result;
}
import { useRouter, useSearchParams } from "next/navigation";
import { ConversationGroup } from "@/lib/conversations-queries";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Search, Filter, X } from "lucide-react";
import { ConversationListItem } from "./conversation-list-item";
import { ScrollArea } from "@/components/ui/scroll-area";

type ConversationsSidebarProps = {
  conversations: ConversationGroup[];
  selectedConversationId: string | null;
  role?: string;
  businesses: Array<{ id: string; name: string }>;
  whatsappNumbers: Array<{
    id: string;
    phone_number: string;
    business_id: string;
  }>;
  canFilterByBusiness: boolean;
  showBusinessColumn: boolean;
  /** Base path for inbox URL updates (e.g. `/businesses/{id}/inbox`). */
  inboxBasePath: string;
  initialFilters: {
    business?: string;
    search?: string;
    dateFrom?: string;
    dateTo?: string;
  };
  /** Shared time tick from layout so every list item's relative timestamp
   * updates on the same minute boundary. */
  now: number;
  onSelectConversation: (conversationId: string) => void;
};

export function ConversationsSidebar({
  conversations,
  selectedConversationId,
  role,
  businesses,
  canFilterByBusiness,
  showBusinessColumn,
  inboxBasePath,
  initialFilters,
  now,
  onSelectConversation,
}: ConversationsSidebarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();
  const [showFilters, setShowFilters] = useState(false);
  // SSR can't read localStorage; populate after mount and listen for cross-tab updates.
  const [lastSeen, setLastSeen] = useState<Record<string, number>>({});

  useEffect(() => {
    setLastSeen(loadLastSeen());
    const onStorage = (e: StorageEvent) => {
      if (!e.key) return;
      if (!e.key.startsWith(LAST_SEEN_PREFIX)) return;
      const id = e.key.slice(LAST_SEEN_PREFIX.length);
      setLastSeen((prev) => ({ ...prev, [id]: Number(e.newValue) }));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const [search, setSearch] = useState(initialFilters.search || "");
  const [business, setBusiness] = useState(initialFilters.business || "all");
  const [datePreset, setDatePreset] = useState("all");

  const hasActiveFilters = search || business !== "all" || datePreset !== "all";

  const applyFilters = () => {
    const params = new URLSearchParams(searchParams.toString());

    if (search) {
      params.set("search", search);
    } else {
      params.delete("search");
    }

    if (business !== "all") {
      params.set("business", business);
    } else {
      params.delete("business");
    }

    if (datePreset !== "all") {
      const now = new Date();
      let dateFrom: Date | null = null;

      switch (datePreset) {
        case "today":
          dateFrom = new Date(now.setHours(0, 0, 0, 0));
          break;
        case "week":
          dateFrom = new Date(now.setDate(now.getDate() - 7));
          break;
        case "month":
          dateFrom = new Date(now.setMonth(now.getMonth() - 1));
          break;
      }

      if (dateFrom) {
        params.set("dateFrom", dateFrom.toISOString());
      }
    } else {
      params.delete("dateFrom");
      params.delete("dateTo");
    }

    startTransition(() => {
      router.push(`${inboxBasePath}?${params.toString()}`);
    });
  };

  const clearFilters = () => {
    setSearch("");
    setBusiness("all");
    setDatePreset("all");

    const params = new URLSearchParams(searchParams.toString());
    params.delete("search");
    params.delete("business");
    params.delete("dateFrom");
    params.delete("dateTo");

    startTransition(() => {
      router.push(`${inboxBasePath}?${params.toString()}`);
    });
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      applyFilters();
    }
  };

  const handleConversationClick = (whatsappId: string, businessId: string) => {
    const conversationId = `${whatsappId}:${businessId}`;
    const now = Date.now(); // eslint-disable-line react-hooks/purity
    setLastSeen((prev) => ({ ...prev, [conversationId]: now }));
    localStorage.setItem(`inbox:lastSeen:${conversationId}`, String(now));

    const params = new URLSearchParams(searchParams.toString());
    params.set("conversation", conversationId);
    // Update URL without re-running the RSC tree.
    window.history.replaceState(
      null,
      "",
      `${inboxBasePath}?${params.toString()}`,
    );

    onSelectConversation(conversationId);
  };

  return (
    <Card className="h-full flex flex-col overflow-hidden">
      {/* Search + Filter header */}
      <CardContent className="p-3 space-y-2 flex-shrink-0 border-b">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <Input
            placeholder="Search conversations..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyPress={handleKeyPress}
            className="pl-9 pr-10 h-9"
          />
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7"
            onClick={() => setShowFilters(!showFilters)}
            aria-label="Toggle filters"
          >
            <Filter
              className={["h-4 w-4", showFilters ? "text-primary" : ""].join(
                " ",
              )}
            />
          </Button>
        </div>

        {showFilters && (
          <div className="space-y-2 pt-1">
            {canFilterByBusiness && (
              <Select value={business} onValueChange={setBusiness}>
                <SelectTrigger className="w-full h-9">
                  <SelectValue placeholder="All businesses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All businesses</SelectItem>
                  {businesses.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            <Select value={datePreset} onValueChange={setDatePreset}>
              <SelectTrigger className="w-full h-9">
                <SelectValue placeholder="Date range" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All time</SelectItem>
                <SelectItem value="today">Today</SelectItem>
                <SelectItem value="week">This week</SelectItem>
                {role !== "member" && (
                  <SelectItem value="month">This month</SelectItem>
                )}
              </SelectContent>
            </Select>

            <div className="flex gap-2">
              <Button
                onClick={applyFilters}
                disabled={isPending}
                size="sm"
                className="flex-1"
              >
                Apply
              </Button>
              {hasActiveFilters && (
                <Button
                  variant="outline"
                  onClick={clearFilters}
                  disabled={isPending}
                  size="sm"
                  aria-label="Clear filters"
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        )}
      </CardContent>

      {/* Conversations list */}
      <ScrollArea className="flex-1">
        <div className="divide-y">
          {conversations.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No conversations found
            </div>
          ) : (
            conversations.map((conversation) => {
              const conversationId = `${conversation.whatsapp_id}:${conversation.business_id}`;
              const seenAt = lastSeen[conversationId];
              const isUnread =
                !seenAt ||
                new Date(conversation.last_timestamp).getTime() > seenAt;
              return (
                <ConversationListItem
                  key={conversationId}
                  conversation={conversation}
                  isSelected={conversationId === selectedConversationId}
                  isUnread={
                    isUnread && conversationId !== selectedConversationId
                  }
                  showBusiness={showBusinessColumn}
                  now={now}
                  onClick={() =>
                    handleConversationClick(
                      conversation.whatsapp_id,
                      conversation.business_id,
                    )
                  }
                />
              );
            })
          )}
        </div>
      </ScrollArea>
    </Card>
  );
}
