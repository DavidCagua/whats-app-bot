"use client"

import type { ReactNode } from "react"
import Image from "next/image"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  SidebarProvider,
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { SwitcherBusiness } from "@/lib/workspace-businesses"
import { BusinessSwitcher } from "./business-switcher"
import { OrdersAttentionBanner } from "./orders-attention-banner"
import logo from "@/app/logo.png"
import { MODULES, type ModuleKey } from "@/lib/modules"
import {
  BookUser,
  CalendarDays,
  ChevronLeft,
  Clock,
  Contact,
  LayoutDashboard,
  MessageSquare,
  Package,
  Scissors,
  Settings,
  ShoppingCart,
  Tag,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react"

const MODULE_ICONS: Record<ModuleKey, LucideIcon> = {
  overview: LayoutDashboard,
  inbox: MessageSquare,
  bookings: CalendarDays,
  availability: Clock,
  orders: ShoppingCart,
  products: Package,
  promotions: Tag,
  services: Scissors,
  customers: Contact,
  staff: Users,
  team: UserCog,
  settings: Settings,
}

function buildNav(businessId: string, enabledModules: string[]) {
  const enabled = new Set(enabledModules)
  return MODULES.filter((m) => m.required || enabled.has(m.key)).map((m) => ({
    href: `/businesses/${businessId}${m.hrefSegment}`,
    label: m.label,
    icon: MODULE_ICONS[m.key],
  }))
}

type BusinessWorkspaceShellProps = {
  businessId: string
  businessName: string
  /** Optional module keys this business has access to, from businesses.enabled_modules. */
  enabledModules: string[]
  switcherBusinesses: SwitcherBusiness[]
  userName: string | null | undefined
  userEmail: string | null | undefined
  isSuperAdmin: boolean
  /** Server action sign-out form (cannot call signOut from this client component). */
  signOutSlot: ReactNode
  initialOrderCounts: { pending: number; inFlight: number; awaitingHandoff: number }
  children: React.ReactNode
}

export function BusinessWorkspaceShell({
  businessId,
  businessName,
  enabledModules,
  switcherBusinesses,
  userName,
  userEmail,
  isSuperAdmin,
  signOutSlot,
  initialOrderCounts,
  children,
}: BusinessWorkspaceShellProps) {
  const pathname = usePathname()
  const items = buildNav(businessId, enabledModules)

  function isNavActive(href: string) {
    if (pathname === href) return true
    const overviewHref = `/businesses/${businessId}`
    if (href === overviewHref) return false
    return pathname.startsWith(`${href}/`)
  }

  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full">
        <Sidebar>
          <SidebarHeader className="border-b px-3 py-3">
            <div className="flex items-center gap-2 px-2">
              <Image
                src={logo}
                alt="Omniabot"
                width={32}
                height={32}
                className="size-8 shrink-0 object-contain"
                priority
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{businessName}</p>
                <p className="text-xs text-muted-foreground">Área de trabajo</p>
              </div>
            </div>
          </SidebarHeader>

          <BusinessSwitcher
            currentBusinessId={businessId}
            businesses={switcherBusinesses}
          />

          <SidebarContent className="px-2 py-2">
            <SidebarMenu>
              {items.map(({ href, label, icon: Icon }) => {
                const active = isNavActive(href)
                return (
                  <SidebarMenuItem key={href}>
                    <SidebarMenuButton asChild isActive={active}>
                      <Link href={href}>
                        <Icon className="h-4 w-4" />
                        <span>{label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarContent>

          <SidebarFooter className="border-t p-3 space-y-2">
            <Button variant="ghost" size="sm" className="w-full justify-start" asChild>
              <Link href="/businesses" className="gap-2">
                <ChevronLeft className="h-4 w-4" />
                Todos los negocios
              </Link>
            </Button>
            {isSuperAdmin && (
              <Button variant="ghost" size="sm" className="w-full justify-start" asChild>
                <Link href="/users" className="gap-2">
                  <BookUser className="h-4 w-4" />
                  Users
                </Link>
              </Button>
            )}
            <div className="flex items-center justify-between gap-2 rounded-md border p-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{userName}</p>
                <p className="truncate text-xs text-muted-foreground">{userEmail}</p>
                {isSuperAdmin && (
                  <span className="text-xs font-medium text-primary">Súper Admin</span>
                )}
              </div>
              {signOutSlot}
            </div>
          </SidebarFooter>
        </Sidebar>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 items-center border-b px-4 md:px-6">
            <SidebarTrigger className={cn("-ml-1")} />
          </div>
          {enabledModules.includes("orders") && (
            <OrdersAttentionBanner
              businessId={businessId}
              initialCounts={initialOrderCounts}
            />
          )}
          <div className="flex-1 overflow-auto p-4 md:p-6">{children}</div>
        </main>
      </div>
    </SidebarProvider>
  )
}
