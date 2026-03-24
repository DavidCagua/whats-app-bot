import { auth } from "@/lib/auth"
import { redirect } from "next/navigation"
import { PlatformHeader } from "@/components/platform-header"

export default async function PlatformLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const session = await auth()
  if (!session) redirect("/login")

  return (
    <div className="flex min-h-screen flex-col">
      <PlatformHeader session={session} />
      <div className="flex-1 p-6">{children}</div>
    </div>
  )
}
