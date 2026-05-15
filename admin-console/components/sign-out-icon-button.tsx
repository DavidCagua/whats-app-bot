import { logoutAction } from "@/lib/actions/logout";
import { LogoutButtonClient } from "@/components/logout-button-client";

export function SignOutIconButton() {
  return <LogoutButtonClient action={logoutAction} />;
}
