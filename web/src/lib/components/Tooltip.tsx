import * as RT from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export function TooltipProvider({ children }: { children: ReactNode }) {
  return <RT.Provider delayDuration={300}>{children}</RT.Provider>;
}

export function Tooltip({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          sideOffset={6}
          className="z-50 rounded-md border border-line2 bg-panel px-2 py-1 text-[11px] text-text shadow-[var(--shadow-lg)] data-[state=delayed-open]:animate-[fade_.12s_ease]"
        >
          {label}
          <RT.Arrow className="fill-[var(--panel)]" />
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  );
}
