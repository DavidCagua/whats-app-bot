"use client"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { DropdownMenuSeparator } from "@/components/ui/dropdown-menu"
import { ChevronsUpDown } from "lucide-react"
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
  <button className="flex w-full items-center gap-3 rounded-lg p-2 hover:bg-muted transition">
    <Avatar className="h-8 w-8">
      <AvatarFallback>
        {userName?.charAt(0)}
      </AvatarFallback>
    </Avatar>

    <div className="flex-1 text-left">
      <p className="truncate text-sm font-medium">{userName}</p>
      <p className="truncate text-xs text-muted-foreground">
        {userEmail}
      </p>
    </div>

    <ChevronsUpDown className="h-4 w-4 text-muted-foreground" />
  </button>
</DropdownMenuTrigger>

      <DropdownMenuContent
  side="right"
  align="end"
  className="w-56"
>
  <div className="flex items-center gap-2 p-2">
    <Avatar className="h-8 w-8">
      <AvatarFallback>
        {userName?.charAt(0) ?? "U"}
      </AvatarFallback>
    </Avatar>

    <div className="flex flex-col leading-none">
      <p className="text-sm font-medium">{userName}</p>
      <p className="text-xs text-muted-foreground">
        {userEmail}
      </p>
    </div>
  </div>

  <DropdownMenuSeparator />


  <DropdownMenuItem asChild>
    <div className="w-full">{signOutSlot}</div>
  </DropdownMenuItem>
</DropdownMenuContent>
    </DropdownMenu>
  )
}