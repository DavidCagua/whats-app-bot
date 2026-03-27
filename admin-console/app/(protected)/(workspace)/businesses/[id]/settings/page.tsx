import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness, isSuperAdmin } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { BusinessSettingsForm } from "./components/business-settings-form"
import { DeleteBusinessButton } from "./components/delete-business-button"
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
  }>
}

export default async function BusinessSettingsPage({
  params,
  searchParams,
}: BusinessSettingsPageProps) {
  const { id } = await params
  const { tab } = await searchParams
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

  const validTabs: SettingsTab[] = ["general", "integrations", "agents"]
  const defaultTab: SettingsTab =
    (tab as SettingsTab) && validTabs.includes(tab as SettingsTab)
      ? (tab as SettingsTab)
      : "general"

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold">Configuración del negocio</h1>
          <p className="text-muted-foreground">
            {canEdit
              ? `Configura los ajustes de ${business.name}`
              : `Ver configuración de ${business.name}`}
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
