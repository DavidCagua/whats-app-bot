import { auth } from "@/lib/auth";
import { redirect } from "next/navigation";
import { canAccessBusiness } from "@/lib/permissions";
import { redirectIfModuleDisabled } from "@/lib/modules";
import {
  getBookingsAccess,
  getAvailabilityRules,
} from "@/lib/bookings-queries";
import { AvailabilityClient } from "./components/availability-client";

export default async function BusinessAvailabilityPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: businessId } = await params;
  const session = await auth();

  if (!session) redirect("/login");
  if (!canAccessBusiness(session, businessId)) redirect("/businesses");
  await redirectIfModuleDisabled(businessId, "availability");

  const access = await getBookingsAccess(session);
  if (
    access.businessIds !== "all" &&
    !access.businessIds.includes(businessId)
  ) {
    redirect("/businesses");
  }
  if (!access.canManageAvailability) {
    redirect(`/businesses/${businessId}`);
  }

  const rules = await getAvailabilityRules(businessId);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          Horario de atención
        </h1>
        <p className="text-sm text-muted-foreground">
          Horarios de atención y duración de turnos para este negocio.
        </p>
      </div>

      <AvailabilityClient businessId={businessId} initialRules={rules} />
    </div>
  );
}
