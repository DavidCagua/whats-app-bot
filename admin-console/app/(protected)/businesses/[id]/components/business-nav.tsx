"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LayoutDashboard, Package, ShoppingCart, Users, Settings } from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { href: (id: string) => `/businesses/${id}`, label: "Overview", icon: LayoutDashboard },
  { href: (id: string) => `/businesses/${id}/products`, label: "Products", icon: Package },
  { href: (id: string) => `/businesses/${id}/orders`, label: "Orders", icon: ShoppingCart },
  { href: (id: string) => `/businesses/${id}/team`, label: "Team", icon: Users },
  { href: (id: string) => `/businesses/${id}/settings`, label: "Settings", icon: Settings },
] as const

interface BusinessNavProps {
  businessId: string
}

export function BusinessNav({ businessId }: BusinessNavProps) {
  const pathname = usePathname()

  return (
    <nav className="flex gap-1 border-b mb-6">
      {navItems.map(({ href, label, icon: Icon }) => {
        const hrefPath = href(businessId)
        const isActive =
          hrefPath === pathname ||
          (hrefPath !== `/businesses/${businessId}` && pathname.startsWith(hrefPath))

        return (
          <Link
            key={label}
            href={hrefPath}
            className={cn(
              "flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors -mb-px border-b-2",
              isActive
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
