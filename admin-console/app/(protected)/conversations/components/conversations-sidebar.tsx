"use client"

import { useState, useTransition } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ConversationGroup } from "@/lib/conversations-queries"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Search, Filter, X } from "lucide-react"
import { ConversationListItem } from "./conversation-list-item"
import { ScrollArea } from "@/components/ui/scroll-area"

type ConversationsSidebarProps = {
  conversations: ConversationGroup[]
  selectedConversationId: string | null
  role?: string
  businesses: Array<{ id: string; name: string }>
  whatsappNumbers: Array<{ id: string; phone_number: string; business_id: string }>
  canFilterByBusiness: boolean
  showBusinessColumn: boolean
  initialFilters: {
    business?: string
    search?: string
    dateFrom?: string
    dateTo?: string
  }
}

export function ConversationsSidebar({
  conversations,
  selectedConversationId,
  role,
  businesses,
  canFilterByBusiness,
  showBusinessColumn,
  initialFilters,
}: ConversationsSidebarProps) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [isPending, startTransition] = useTransition()
  const [showFilters, setShowFilters] = useState(false)

  const [search, setSearch] = useState(initialFilters.search || "")
  const [business, setBusiness] = useState(initialFilters.business || "all")
  const [datePreset, setDatePreset] = useState("all")

  const hasActiveFilters = search || business !== "all" || datePreset !== "all"

  const applyFilters = () => {
    const params = new URLSearchParams(searchParams.toString())

    if (search) {
      params.set("search", search)
    } else {
      params.delete("search")
    }

    if (business !== "all") {
      params.set("business", business)
    } else {
      params.delete("business")
    }

    if (datePreset !== "all") {
      const now = new Date()
      let dateFrom: Date | null = null

      switch (datePreset) {
        case "today":
          dateFrom = new Date(now.setHours(0, 0, 0, 0))
          break
        case "week":
          dateFrom = new Date(now.setDate(now.getDate() - 7))
          break
        case "month":
          dateFrom = new Date(now.setMonth(now.getMonth() - 1))
          break
      }

      if (dateFrom) {
        params.set("dateFrom", dateFrom.toISOString())
      }
    } else {
      params.delete("dateFrom")
      params.delete("dateTo")
    }

    startTransition(() => {
      router.push(`/conversations?${params.toString()}`)
    })
  }

  const clearFilters = () => {
    setSearch("")
    setBusiness("all")
    setDatePreset("all")

    const params = new URLSearchParams(searchParams.toString())
    params.delete("search")
    params.delete("business")
    params.delete("dateFrom")
    params.delete("dateTo")

    startTransition(() => {
      router.push(`/conversations?${params.toString()}`)
    })
  }

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      applyFilters()
    }
  }

  const handleConversationClick = (whatsappId: string, businessId: string) => {
    const params = new URLSearchParams(searchParams.toString())
    params.set("conversation", `${whatsappId}:${businessId}`)

    startTransition(() => {
      router.push(`/conversations?${params.toString()}`)
    })
  }

  return (
    <Card className="h-full flex flex-col">
      <CardContent className="p-4 space-y-3 flex-shrink-0">
        {/* Search Bar */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search conversations..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyPress={handleKeyPress}
            className="pl-9 pr-10"
          />
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-1 top-1/2 transform -translate-y-1/2 h-7 w-7"
            onClick={() => setShowFilters(!showFilters)}
          >
            <Filter className="h-4 w-4" />
          </Button>
        </div>

        {/* Filters (collapsible) */}
        {showFilters && (
          <div className="space-y-2 pt-2 border-t">
            {canFilterByBusiness && (
              <Select value={business} onValueChange={setBusiness}>
                <SelectTrigger className="w-full">
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
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Date range" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All time</SelectItem>
                <SelectItem value="today">Today</SelectItem>
                <SelectItem value="week">This week</SelectItem>
                {role !== "staff" && <SelectItem value="month">This month</SelectItem>}
              </SelectContent>
            </Select>

            <div className="flex gap-2">
              <Button onClick={applyFilters} disabled={isPending} size="sm" className="flex-1">
                Apply
              </Button>
              {hasActiveFilters && (
                <Button
                  variant="outline"
                  onClick={clearFilters}
                  disabled={isPending}
                  size="sm"
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        )}
      </CardContent>

      {/* Conversations List */}
      <ScrollArea className="flex-1">
        <div className="divide-y">
          {conversations.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              No conversations found
            </div>
          ) : (
            conversations.map((conversation) => {
              const conversationId = `${conversation.whatsapp_id}:${conversation.business_id}`
              return (
                <ConversationListItem
                  key={conversationId}
                  conversation={conversation}
                  isSelected={conversationId === selectedConversationId}
                  showBusiness={showBusinessColumn}
                  onClick={() =>
                    handleConversationClick(
                      conversation.whatsapp_id,
                      conversation.business_id
                    )
                  }
                />
              )
            })
          )}
        </div>
      </ScrollArea>
    </Card>
  )
}
