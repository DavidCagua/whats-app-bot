"use client"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Building2, Plug, Bot } from "lucide-react"
import { ReactNode } from "react"

export type SettingsTab = "general" | "integrations" | "agents"

interface SettingsTabsProps {
  defaultTab?: SettingsTab
  generalContent: ReactNode
  integrationsContent: ReactNode
  agentsContent: ReactNode
}

export function SettingsTabs({
  defaultTab = "general",
  generalContent,
  integrationsContent,
  agentsContent,
}: SettingsTabsProps) {
  return (
    <Tabs defaultValue={defaultTab} className="w-full">
      <TabsList className="mb-6">
        <TabsTrigger value="general" className="gap-2">
          <Building2 className="h-4 w-4" />
          General
        </TabsTrigger>
        <TabsTrigger value="integrations" className="gap-2">
          <Plug className="h-4 w-4" />
          Integraciones
        </TabsTrigger>
        <TabsTrigger value="agents" className="gap-2">
          <Bot className="h-4 w-4" />
          Agentes
        </TabsTrigger>
      </TabsList>
      <TabsContent value="general" className="space-y-6">
        {generalContent}
      </TabsContent>
      <TabsContent value="integrations" className="space-y-6">
        {integrationsContent}
      </TabsContent>
      <TabsContent value="agents" className="space-y-6">
        {agentsContent}
      </TabsContent>
    </Tabs>
  )
}
