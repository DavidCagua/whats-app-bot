import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";
import { getPostLoginRedirectPath } from "@/lib/post-login-redirect";

export default async function RootDashboardPage() {
  const session = await auth();
  if (!session) redirect("/login");
  redirect(getPostLoginRedirectPath(session));
}
