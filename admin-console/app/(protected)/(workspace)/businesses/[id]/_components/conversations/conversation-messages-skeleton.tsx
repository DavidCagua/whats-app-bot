"use client"

import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { ArrowLeft } from "lucide-react"
import { cn } from "@/lib/utils"

type ConversationMessagesSkeletonProps = {
  onBack?: () => void
}

const BUBBLE_WIDTHS = ["w-3/5", "w-2/5", "w-3/4", "w-1/2", "w-2/3", "w-1/3"]

export function ConversationMessagesSkeleton({
  onBack,
}: ConversationMessagesSkeletonProps) {
  return (
    <Card
      className="h-full flex flex-col overflow-hidden"
      aria-busy="true"
      aria-live="polite"
    >
      {/* Header */}
      <CardHeader className="border-b p-3 sm:p-4 flex-shrink-0">
        <div className="flex items-center gap-2 sm:gap-3">
          {onBack && (
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden flex-shrink-0 h-8 w-8"
              onClick={onBack}
              aria-label="Back to conversations"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <Skeleton className="h-9 w-9 sm:h-10 sm:w-10 rounded-full flex-shrink-0" />
          <div className="flex-1 min-w-0 space-y-2">
            <Skeleton className="h-4 w-40" />
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-3 w-16" />
            </div>
          </div>
        </div>
      </CardHeader>

      {/* Messages */}
      <CardContent className="p-3 sm:p-4 flex-1 overflow-hidden">
        <div className="space-y-3 sm:space-y-4">
          {BUBBLE_WIDTHS.map((width, i) => {
            const isAssistant = i % 2 === 1
            return (
              <div
                key={i}
                className={cn(
                  "flex gap-2 sm:gap-3",
                  isAssistant && "flex-row-reverse"
                )}
              >
                <Skeleton className="h-7 w-7 sm:h-8 sm:w-8 rounded-full flex-shrink-0 mt-1" />
                <div
                  className={cn(
                    "flex-1 space-y-1 max-w-[80%] sm:max-w-[75%]",
                    isAssistant && "flex flex-col items-end"
                  )}
                >
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className={cn("h-10 rounded-2xl sm:rounded-lg", width)} />
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>

      {/* Composer */}
      <div className="border-t p-3 flex-shrink-0">
        <div className="flex gap-2 items-end">
          <Skeleton className="h-9 w-9 flex-shrink-0" />
          <Skeleton className="h-9 flex-1" />
          <Skeleton className="h-9 w-16" />
        </div>
      </div>
    </Card>
  )
}
