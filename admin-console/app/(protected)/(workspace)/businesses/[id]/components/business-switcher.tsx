"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { SwitcherBusiness } from "@/lib/workspace-businesses";

type BusinessSwitcherProps = {
  currentBusinessId: string;
  businesses: SwitcherBusiness[];
};

export function BusinessSwitcher({
  currentBusinessId,
  businesses,
}: BusinessSwitcherProps) {
  const pathname = usePathname();
  const router = useRouter();

  if (businesses.length <= 1) {
    return null;
  }

  function onChange(nextId: string) {
    const nextPath = pathname.replace(
      /^\/businesses\/[^/]+/,
      `/businesses/${nextId}`,
    );
    router.push(nextPath);
  }

  return (
    <div className="px-2 py-2">
      <p className="mb-1.5 px-2 text-xs font-medium text-muted-foreground">
        Business
      </p>
      <Select value={currentBusinessId} onValueChange={onChange}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Select business" />
        </SelectTrigger>
        <SelectContent>
          {businesses.map((b) => (
            <SelectItem key={b.id} value={b.id}>
              {b.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
