"use client";

import { useState } from "react";
import type { AvailabilityRule } from "@/lib/bookings-queries";
import { AvailabilitySettings } from "../../_components/bookings/availability-settings";

export function AvailabilityClient({
  businessId,
  initialRules,
}: {
  businessId: string;
  initialRules: AvailabilityRule[];
}) {
  const [rules, setRules] = useState<AvailabilityRule[]>(initialRules);
  return (
    <AvailabilitySettings
      businessId={businessId}
      initialRules={rules}
      onRulesUpdated={setRules}
    />
  );
}
