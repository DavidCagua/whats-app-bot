import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function InboxLoading() {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Skeleton className="h-7 w-32" />
        <Skeleton className="h-4 w-72" />
      </div>

      <Card>
        <CardContent className="p-3 flex flex-wrap gap-4">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-5 w-28" />
          <Skeleton className="h-5 w-32" />
        </CardContent>
      </Card>

      <div className="h-[calc(100vh-56px-48px)] flex gap-4 min-h-[420px]">
        {/* Sidebar skeleton */}
        <div className="flex flex-col w-full md:w-[340px] lg:w-[380px] md:flex-shrink-0">
          <Card className="h-full flex flex-col overflow-hidden">
            <CardContent className="p-3 space-y-2 border-b">
              <Skeleton className="h-9 w-full" />
            </CardContent>
            <div className="flex-1 divide-y">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="p-3 space-y-2">
                  <div className="flex justify-between gap-2">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-10" />
                  </div>
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-3/4" />
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Right pane skeleton */}
        <div className="flex-1 min-w-0 hidden md:flex md:flex-col">
          <Card className="h-full flex items-center justify-center">
            <Skeleton className="h-10 w-48" />
          </Card>
        </div>
      </div>
    </div>
  );
}
