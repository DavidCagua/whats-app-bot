import Image from "next/image"
import Link from "next/link"
import type { Session } from "next-auth"
import { Building2, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { signOut } from "@/lib/auth"
import logo from "@/app/logo.png"

export async function PlatformHeader({ session }: { session: Session }) {
  const isSuperAdmin = session.user?.role === "super_admin"

  return (
    <header className="flex h-14 items-center justify-between border-b px-4 md:px-6">
      <div className="flex items-center gap-3">
        <Image
          src={logo}
          alt="Omniabot"
          width={32}
          height={32}
          className="size-8 shrink-0 object-contain"
          priority
        />
        <nav className="flex items-center gap-1 text-sm">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/businesses" className="gap-2">
              <Building2 className="h-4 w-4" />
              Businesses
            </Link>
          </Button>
          {isSuperAdmin && (
            <Button variant="ghost" size="sm" asChild>
              <Link href="/users" className="gap-2">
                <Users className="h-4 w-4" />
                Users
              </Link>
            </Button>
          )}
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden text-right text-sm sm:block">
          <p className="font-medium leading-none">{session.user?.name}</p>
          <p className="text-xs text-muted-foreground">{session.user?.email}</p>
        </div>
        <form
          action={async () => {
            "use server"
            await signOut({ redirectTo: "/login" })
          }}
        >
          <Button variant="outline" size="sm" type="submit">
            Sign out
          </Button>
        </form>
      </div>
    </header>
  )
}
