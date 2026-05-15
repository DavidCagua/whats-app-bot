"use client";

import { Button } from "@/components/ui/button";
import { Loader2, LogOut } from "lucide-react";
import { useActionState } from "react";

export function LogoutButtonClient({ action }: any) {
  const [state, formAction, pending] = useActionState(action, { error: null });

  return (
    <form action={formAction} className="flex flex-col gap-1">
      <Button
        type="submit"
        variant="ghost"
        disabled={pending}
        className="flex items-center gap-2"
      >
        {pending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Cerrando...
          </>
        ) : (
          <>
            <LogOut className="h-4 w-4" />
            Cerrar sesión
          </>
        )}
      </Button>

      {state?.error && (
        <span className="text-xs text-red-500">{state.error}</span>
      )}
    </form>
  );
}
