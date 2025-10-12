import { prisma } from "@/lib/prisma"
import { notFound } from "next/navigation"
import { BusinessSettingsForm } from "./components/business-settings-form"
import { getBusinessSettings } from "@/lib/actions/business-settings"

interface BusinessSettingsPageProps {
  params: {
    id: string
  }
}

export default async function BusinessSettingsPage({ params }: BusinessSettingsPageProps) {
  const { id } = await params
  
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Business Settings</h1>
        <p className="text-muted-foreground">
          Configure settings for {business.name}
        </p>
      </div>

      <BusinessSettingsForm business={business} initialSettings={settings} />
    </div>
  )
}
