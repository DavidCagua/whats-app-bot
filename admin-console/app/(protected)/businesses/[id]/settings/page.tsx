import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, canEditBusiness, isSuperAdmin } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { BusinessSettingsForm } from "./components/business-settings-form"
import { DeleteBusinessButton } from "./components/delete-business-button"
import { getBusinessSettings } from "@/lib/actions/business-settings"

interface BusinessSettingsPageProps {
  params: {
    id: string
  }
}

export default async function BusinessSettingsPage({ params }: BusinessSettingsPageProps) {
  const { id } = await params
  const session = await auth()

  // Check if user can access this business
  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({
    where: {
      id,
    },
  })

  if (!business) {
    notFound()
  }

  // Get the business settings using our server action
  const settings = await getBusinessSettings(id)

  if (!settings) {
    notFound()
  }

  const canEdit = canEditBusiness(session, id)
  const canDelete = isSuperAdmin(session)

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

      <BusinessSettingsForm
        business={business}
        initialSettings={settings}
        readOnly={!canEdit}
      />
    </div>
  )
}
