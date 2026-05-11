import { prisma } from "@/lib/prisma"
import { auth } from "@/lib/auth"
import { canAccessBusiness, isSuperAdmin } from "@/lib/permissions"
import { notFound, redirect } from "next/navigation"
import { getWorkspaceSwitcherBusinesses } from "@/lib/workspace-businesses"
import { getOrderBannerCounts } from "@/lib/orders-queries"
import { SignOutIconButton } from "@/components/sign-out-icon-button"
import { BusinessWorkspaceShell } from "./components/business-workspace-shell"

interface BusinessLayoutProps {
  children: React.ReactNode
  params: Promise<{ id: string }>
}

export default async function BusinessLayout({ children, params }: BusinessLayoutProps) {
  const { id } = await params
  const session = await auth()

  if (!session) {
    redirect("/login")
  }

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses")
  }

  const business = await prisma.businesses.findUnique({
    where: { id },
  })

  if (!business) {
    notFound()
  }

  const ordersEnabled = business.enabled_modules.includes("orders")
  const [switcherBusinesses, orderCounts] = await Promise.all([
    getWorkspaceSwitcherBusinesses(session),
    ordersEnabled
      ? getOrderBannerCounts(id)
      : Promise.resolve({ pending: 0, inFlight: 0, awaitingHandoff: 0 }),
  ])

  return (
    <BusinessWorkspaceShell
      businessId={id}
      businessName={business.name}
      enabledModules={business.enabled_modules}
      switcherBusinesses={switcherBusinesses}
      userName={session.user?.name}
      userEmail={session.user?.email}
      isSuperAdmin={isSuperAdmin(session)}
      signOutSlot={<SignOutIconButton />}
      initialOrderCounts={orderCounts}
    >
      {children}
    </BusinessWorkspaceShell>
  )
}
