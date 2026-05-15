import { prisma } from "@/lib/prisma";
import { auth } from "@/lib/auth";
import { canAccessBusiness, canEditBusiness } from "@/lib/permissions";
import { notFound, redirect } from "next/navigation";
import { getBusinessUsers } from "@/lib/actions/users";
import { TeamTable } from "./components/team-table";
import { InviteUserButton } from "./components/invite-user-button";

interface BusinessTeamPageProps {
  params: Promise<{ id: string }>;
}

export default async function BusinessTeamPage({
  params,
}: BusinessTeamPageProps) {
  const { id } = await params;
  const session = await auth();

  if (!canAccessBusiness(session, id)) {
    redirect("/businesses");
  }

  const business = await prisma.businesses.findUnique({
    where: { id },
  });

  if (!business) {
    notFound();
  }

  const users = await getBusinessUsers(id);
  const canInvite = canEditBusiness(session, id);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Acceso</h1>
          <p className="text-muted-foreground">
            Quién puede iniciar sesión en esta consola para {business.name}
          </p>
        </div>
        {canInvite && (
          <InviteUserButton businessId={id} businessName={business.name} />
        )}
      </div>

      <TeamTable data={users} businessId={id} canEdit={canInvite} />
    </div>
  );
}
