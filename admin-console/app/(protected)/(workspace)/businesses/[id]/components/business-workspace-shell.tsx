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
import { UserNav } from "@/components/user-nav"
import type { SwitcherBusiness } from "@/lib/workspace-businesses"
import { BusinessSwitcher } from "./business-switcher"
import logo from "@/app/logo.png"
import {
  BookUser,
  CalendarDays,
  ChevronLeft,
  LayoutDashboard,
  MessageSquare,
  Package,
  Scissors,
  Settings,
  ShoppingCart,
  UserCog,
  Users,
} from "lucide-react"

const nav = (id: string) =>
  [
    {
      href: `/businesses/${id}`,
      label: "Resumen",
      icon: LayoutDashboard,
    },
    {
      href: `/businesses/${id}/inbox`,
      label: "Bandeja de entrada",
      icon: MessageSquare,
    },
    {
      href: `/businesses/${id}/bookings`,
      label: "Reservas",
      icon: CalendarDays,
    },
    {
      href: `/businesses/${id}/orders`,
      label: "Pedidos",
      icon: ShoppingCart,
    },
    {
      href: `/businesses/${id}/products`,
      label: "Productos",
      icon: Package,
    },
    {
      href: `/businesses/${id}/services`,
      label: "Servicios",
      icon: Scissors,
    },
    {
      href: `/businesses/${id}/staff`,
      label: "Personal",
      icon: Users,
    },
    {
      href: `/businesses/${id}/team`,
      label: "Acceso",
      icon: UserCog,
    },
    {
      href: `/businesses/${id}/settings`,
      label: "Configuración",
      icon: Settings,
    },
  ] as const

type BusinessWorkspaceShellProps = {
  businessId: string
  businessName: string
  switcherBusinesses: SwitcherBusiness[]
  userName: string | null | undefined
  userEmail: string | null | undefined
  isSuperAdmin: boolean
  /** Server action sign-out form (cannot call signOut from this client component). */
  signOutSlot: ReactNode
  children: React.ReactNode
}

export function BusinessWorkspaceShell({
  businessId,
  businessName,
  switcherBusinesses,
  userName,
  userEmail,
  isSuperAdmin,
  signOutSlot,
  children,
}: BusinessWorkspaceShellProps) {
  const pathname = usePathname()
  const items = nav(businessId)

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

  <UserNav
    userName={userName}
    userEmail={userEmail}
    signOutSlot={signOutSlot}
  />
</SidebarFooter>
        </Sidebar>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 items-center border-b px-4 md:px-6">
            <SidebarTrigger className={cn("-ml-1")} />
          </div>
          <div className="flex-1 overflow-auto p-4 md:p-6">{children}</div>
        </main>
      </div>
    </SidebarProvider>
  )
}
