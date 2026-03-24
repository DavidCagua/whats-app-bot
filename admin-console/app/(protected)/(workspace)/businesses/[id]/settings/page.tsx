import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness, isSuperAdmin } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { BusinessSettingsForm } from "./components/business-settings-form"
import { DeleteBusinessButton } from "./components/delete-business-button"
import { GoogleCalendarSettings } from "./components/google-calendar-settings"
import { WhatsAppSettings } from "./components/whatsapp-settings"
import { AgentsSettingsForm } from "./components/agents-settings-form"
import { SettingsTabs, type SettingsTab } from "./components/settings-tabs"
import { getBusinessSettings } from "@/lib/actions/business-settings"
import { getBusinessAgents } from "@/lib/actions/business-agents"

interface BusinessSettingsPageProps {
  params: Promise<{
    id: string
  }>
  searchParams: Promise<{
    tab?: string
    calendar_connected?: string
    calendar_error?: string
  }>
}

export default async function BusinessSettingsPage({
  params,
  searchParams,
}: BusinessSettingsPageProps) {
  const { id } = await params
  const { tab, calendar_connected, calendar_error } = await searchParams
  const session = await auth()

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({
    where: { id },
  })

  if (!business) {
    notFound()
  }

  const [settings, agents] = await Promise.all([
    getBusinessSettings(id),
    getBusinessAgents(id),
  ])

  if (!settings) {
    notFound()
  }

  const canEdit = canEditBusiness(session, id)
  const canDelete = isSuperAdmin(session)

  const calendarConnected = calendar_connected === "true"
  const calendarError = calendar_error

  // When returning from calendar OAuth, open Integrations tab
  const validTabs: SettingsTab[] = ["general", "integrations", "agents"]
  const defaultTab: SettingsTab =
    (tab as SettingsTab) && validTabs.includes(tab as SettingsTab)
      ? (tab as SettingsTab)
      : calendar_connected !== undefined || calendar_error !== undefined
        ? "integrations"
        : "general"

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold">Business Settings</h1>
          <p className="text-muted-foreground">
            {canEdit
              ? `Configure settings for ${business.name}`
              : `View settings for ${business.name}`}
          </p>
        </div>
        {canDelete && (
          <DeleteBusinessButton businessId={id} businessName={business.name} />
        )}
      </div>

      <SettingsTabs
        defaultTab={defaultTab}
        generalContent={
          <BusinessSettingsForm
            business={business}
            initialSettings={settings}
            readOnly={!canEdit}
          />
        }
        integrationsContent={
          <>
            {isSuperAdmin(session) && <WhatsAppSettings businessId={id} />}
            <GoogleCalendarSettings
              businessId={id}
              readOnly={!canEdit}
              showSuccessMessage={calendarConnected}
              errorMessage={calendarError}
            />
          </>
        }
        agentsContent={
          <AgentsSettingsForm
            businessId={id}
            initialAgents={agents}
            readOnly={!canEdit}
          />
        }
      />
    </div>
  )
}
