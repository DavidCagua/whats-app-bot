"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Loader2, Bot, Save } from "lucide-react"
import { toast } from "sonner"
import {
  getBusinessAgents,
  updateBusinessAgents,
  type BusinessAgentConfig,
  type AgentType,
} from "@/lib/actions/business-agents"

interface AgentsSettingsFormProps {
  businessId: string
  readOnly?: boolean
  initialAgents?: BusinessAgentConfig[] | null
}

export function AgentsSettingsForm({
  businessId,
  readOnly = false,
  initialAgents = null,
}: AgentsSettingsFormProps) {
  const [agents, setAgents] = useState<BusinessAgentConfig[]>([])
  const [isLoading, setIsLoading] = useState(!initialAgents)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (initialAgents) {
      setAgents(initialAgents)
      setIsLoading(false)
      return
    }
    async function fetchAgents() {
      try {
        const data = await getBusinessAgents(businessId)
        if (data) setAgents(data)
      } catch (err) {
        console.error("Error fetching agents:", err)
        toast.error("Failed to load agents")
      } finally {
        setIsLoading(false)
      }
    }
    fetchAgents()
  }, [businessId, initialAgents])

  const handleToggle = (agentType: AgentType, enabled: boolean) => {
    setAgents((prev) =>
      prev.map((a) => (a.agent_type === agentType ? { ...a, enabled } : a))
    )
  }

  const handleSave = async () => {
    const enabledCount = agents.filter((a) => a.enabled).length
    if (enabledCount === 0) {
      toast.error("At least one agent must be enabled")
      return
    }

    setIsSaving(true)
    try {
      const result = await updateBusinessAgents(
        businessId,
        agents.map((a) => ({
          agent_type: a.agent_type,
          enabled: a.enabled,
          priority: a.priority,
        }))
      )
      if (result.success) {
        toast.success("Agents updated successfully")
      } else {
        toast.error(result.error || "Failed to update agents")
      }
    } catch {
      toast.error("An error occurred")
    } finally {
      setIsSaving(false)
    }
  }

  const hasChanges = () => {
    if (!initialAgents) return true
    return agents.some(
      (a, i) =>
        initialAgents[i]?.enabled !== a.enabled ||
        initialAgents[i]?.priority !== a.priority
    )
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-5 w-5" />
            AI Agents
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="h-5 w-5" />
          AI Agents
        </CardTitle>
        <CardDescription>
          Enable or disable agents for this business. Each agent handles different types of
          customer requests (bookings, orders, support, etc.).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-4">
          {agents.map((agent) => (
            <div
              key={agent.agent_type}
              className="flex items-start justify-between gap-4 rounded-lg border p-4"
            >
              <div className="flex-1 space-y-1">
                <Label htmlFor={`agent-${agent.agent_type}`} className="text-base font-medium">
                  {agent.name}
                </Label>
                <p className="text-sm text-muted-foreground">{agent.description}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {readOnly ? (
                  <span
                    className={`text-sm font-medium ${agent.enabled ? "text-green-600" : "text-muted-foreground"}`}
                  >
                    {agent.enabled ? "Enabled" : "Disabled"}
                  </span>
                ) : (
                  <Switch
                    id={`agent-${agent.agent_type}`}
                    checked={agent.enabled}
                    onCheckedChange={(checked) => handleToggle(agent.agent_type, checked)}
                  />
                )}
              </div>
            </div>
          ))}
        </div>

        {!readOnly && (
          <div className="flex justify-end pt-2">
            <Button onClick={handleSave} disabled={isSaving || !hasChanges()}>
              {isSaving ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              Save Agents
            </Button>
          </div>
        )}

        <div className="text-sm text-muted-foreground pt-2 border-t">
          <p className="font-medium mb-1">How agents work:</p>
          <ul className="list-disc list-inside space-y-1">
            <li>Incoming messages are routed to the appropriate agent based on intent</li>
            <li>If only one agent is enabled, all messages go to it</li>
            <li>Session state prevents reclassification in multi-turn flows</li>
          </ul>
        </div>
      </CardContent>
    </Card>
  )
}
