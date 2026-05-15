"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import {
  ConversationGroup,
  ConversationThread,
} from "@/lib/conversations-queries";
import { useEventSource } from "@/lib/use-event-source";
import { ConversationsSidebar } from "./conversations-sidebar";
import { ConversationMessagesPanel } from "./conversation-messages-panel";
import { ConversationMessagesSkeleton } from "./conversation-messages-skeleton";
import { Card } from "@/components/ui/card";
import { MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

const RELATIVE_TIME_TICK_MS = 60_000;

type ConversationsLayoutProps = {
  conversations: ConversationGroup[];
  selectedThread: ConversationThread | null;
  role?: string;
  businesses: Array<{ id: string; name: string }>;
  whatsappNumbers: Array<{
    id: string;
    phone_number: string;
    business_id: string;
  }>;
  canFilterByBusiness: boolean;
  showBusinessColumn: boolean;
  /** When set, the SSE list stream is always scoped to this business. */
  scopedBusinessId?: string;
  inboxBasePath: string;
  initialFilters: {
    business?: string;
    search?: string;
    dateFrom?: string;
    dateTo?: string;
  };
};

export function ConversationsLayout({
  conversations: initialConversations,
  selectedThread: initialThread,
  role,
  businesses,
  whatsappNumbers,
  canFilterByBusiness,
  showBusinessColumn,
  scopedBusinessId,
  inboxBasePath,
  initialFilters,
}: ConversationsLayoutProps) {
  const searchParams = useSearchParams();

  const [conversations, setConversations] =
    useState<ConversationGroup[]>(initialConversations);
  const [thread, setThread] = useState<ConversationThread | null>(
    initialThread,
  );
  const [selectedConversationId, setSelectedConversationId] = useState<
    string | null
  >(() => {
    if (initialThread)
      return `${initialThread.whatsapp_id}:${initialThread.business_id}`;
    return searchParams.get("conversation");
  });
  const [threadLoading, setThreadLoading] = useState(false);
  const [mobileView, setMobileView] = useState<"list" | "chat">(
    initialThread ? "chat" : "list",
  );
  const [now, setNow] = useState(() => Date.now());
  const messagesPanelRef = useRef<HTMLDivElement>(null);

  const selectedIdRef = useRef(selectedConversationId);
  useEffect(() => {
    selectedIdRef.current = selectedConversationId;
  }, [selectedConversationId]);

  // One ticker for every list item's relative timestamp; saves N renders/min.
  useEffect(() => {
    const interval = setInterval(
      () => setNow(Date.now()),
      RELATIVE_TIME_TICK_MS,
    );
    return () => clearInterval(interval);
  }, []);

  // Move focus to the messages panel when switching into chat view on mobile.
  useEffect(() => {
    if (mobileView !== "chat") return;
    if (typeof window === "undefined") return;
    if (window.matchMedia("(min-width: 768px)").matches) return;
    requestAnimationFrame(() => {
      messagesPanelRef.current?.focus();
    });
  }, [mobileView, selectedConversationId]);

  // Sync list when filter changes cause an RSC re-run.
  useEffect(() => {
    setConversations(initialConversations);
  }, [initialConversations]);

  const listBusinessId =
    scopedBusinessId ?? searchParams.get("business") ?? null;
  const search = searchParams.get("search");
  const dateFrom = searchParams.get("dateFrom");
  const dateTo = searchParams.get("dateTo");

  const listStreamUrl = useMemo(() => {
    if (!listBusinessId) return null;
    const qs = new URLSearchParams({ businessId: listBusinessId });
    if (search) qs.set("search", search);
    if (dateFrom) qs.set("dateFrom", dateFrom);
    if (dateTo) qs.set("dateTo", dateTo);
    return `/api/conversations/stream?${qs.toString()}`;
  }, [listBusinessId, search, dateFrom, dateTo]);

  const threadStreamUrl = useMemo(() => {
    if (!selectedConversationId) return null;
    const [whatsappId, businessId] = selectedConversationId.split(":");
    if (!whatsappId || !businessId) return null;
    return `/api/conversations/stream?businessId=${encodeURIComponent(businessId)}&whatsappId=${encodeURIComponent(whatsappId)}`;
  }, [selectedConversationId]);

  const onListSnapshot = useCallback((data: ConversationGroup[]) => {
    setConversations(data);
  }, []);

  const onThreadSnapshot = useCallback((data: ConversationThread | null) => {
    if (!data) return;
    const id = `${data.whatsapp_id}:${data.business_id}`;
    if (selectedIdRef.current !== id) return;
    setThread(data);
    setThreadLoading(false);
  }, []);

  useEventSource<ConversationGroup[]>(
    listStreamUrl,
    "snapshot",
    onListSnapshot,
  );
  useEventSource<ConversationThread | null>(
    threadStreamUrl,
    "snapshot",
    onThreadSnapshot,
  );

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      if (conversationId === selectedConversationId) {
        setMobileView("chat");
        return;
      }
      setSelectedConversationId(conversationId);
      setMobileView("chat");
      setThread(null);
      setThreadLoading(true);
    },
    [selectedConversationId],
  );

  const handleBack = () => {
    setMobileView("list");
  };

  return (
    // Workspace: top bar h-14 + main padding p-6 (24px * 2)
    <div className="h-[calc(100vh-56px-48px)] flex gap-4 min-h-[420px]">
      {/* Sidebar — full width on mobile, fixed width on md+, hidden on mobile when in chat view */}
      <div
        className={cn(
          "flex flex-col min-w-0",
          "w-full md:w-[340px] lg:w-[380px] md:flex-shrink-0",
          mobileView === "chat" ? "hidden md:flex" : "flex",
        )}
      >
        <ConversationsSidebar
          conversations={conversations}
          selectedConversationId={selectedConversationId}
          role={role}
          businesses={businesses}
          whatsappNumbers={whatsappNumbers}
          canFilterByBusiness={canFilterByBusiness}
          showBusinessColumn={showBusinessColumn}
          inboxBasePath={inboxBasePath}
          initialFilters={initialFilters}
          now={now}
          onSelectConversation={handleSelectConversation}
        />
      </div>

      {/* Messages panel — hidden on mobile when in list view */}
      <div
        ref={messagesPanelRef}
        tabIndex={-1}
        className={cn(
          "flex-1 min-w-0 outline-none",
          mobileView === "list"
            ? "hidden md:flex md:flex-col"
            : "flex flex-col",
        )}
      >
        {threadLoading && !thread ? (
          <ConversationMessagesSkeleton onBack={handleBack} />
        ) : thread ? (
          <ConversationMessagesPanel thread={thread} onBack={handleBack} />
        ) : (
          <Card className="h-full flex items-center justify-center">
            <div className="text-center text-muted-foreground px-6">
              <MessageSquare className="h-14 w-14 mx-auto mb-4 opacity-20" />
              <p className="text-base font-medium">Select a conversation</p>
              <p className="text-sm mt-1">
                Choose a conversation from the list to view messages
              </p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
