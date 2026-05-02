"use client"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function UserNav({
  userName,
  userEmail,
  signOutSlot,
}: {
  userName: string | null | undefined
  userEmail: string | null | undefined
  signOutSlot: React.ReactNode
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex w-full items-center gap-2 rounded-md border p-2 hover:bg-muted">
          <Avatar className="h-6 w-6">
            <AvatarFallback>
              {userName?.charAt(0) ?? "U"}
            </AvatarFallback>
          </Avatar>

          <div className="min-w-0 flex-1 text-left">
            <p className="truncate text-xs font-medium">{userName}</p>
            <p className="truncate text-xs text-muted-foreground">
              {userEmail}
            </p>
          </div>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem>
          Perfil
        </DropdownMenuItem>

        <DropdownMenuItem asChild>
          {signOutSlot}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}