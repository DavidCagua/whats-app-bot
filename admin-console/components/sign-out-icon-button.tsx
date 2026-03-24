import { signOut } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { LogOut } from "lucide-react"

export function SignOutIconButton() {
  return (
    <form
      action={async () => {
        "use server"
        await signOut({ redirectTo: "/login" })
      }}
    >
      <Button type="submit" variant="ghost" size="icon" className="shrink-0" aria-label="Sign out">
        <LogOut className="h-4 w-4" />
      </Button>
    </form>
  )
}
