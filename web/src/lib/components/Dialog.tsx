import * as RD from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Icon } from "@/lib/icons";

// Accessible modal/drawer built on Radix Dialog: focus trap, ESC, scroll lock, a11y.
// `side` renders it as a right-hand drawer (detail panels) or a centered modal.
export function Dialog({
  open, onOpenChange, title, subtitle, children, footer, side = false, className,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  side?: boolean;
  className?: string;
}) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-black/55 backdrop-blur-[2px] data-[state=open]:animate-[fade_.15s_ease]" />
        <RD.Content
          className={cn(
            "fixed z-50 flex flex-col border-line bg-panel text-text shadow-[var(--shadow-lg)] outline-none",
            side
              ? "right-0 top-0 h-full w-full max-w-[min(560px,92vw)] border-l data-[state=open]:animate-[slideIn_.18s_ease]"
              : "left-1/2 top-1/2 max-h-[85vh] w-[min(620px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-[12px] border data-[state=open]:animate-[fade_.16s_ease]",
            className,
          )}
        >
          {(title || subtitle) && (
            <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
              <div className="min-w-0">
                {title && <RD.Title className="truncate text-[15px] font-semibold">{title}</RD.Title>}
                {subtitle && <RD.Description className="mt-0.5 truncate font-mono text-[11px] text-faint">{subtitle}</RD.Description>}
              </div>
              <RD.Close className="grid size-7 shrink-0 place-items-center rounded-md text-mut transition hover:bg-panel3 hover:text-text">
                <Icon n="close" />
              </RD.Close>
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
          {footer && <div className="border-t border-line px-5 py-3">{footer}</div>}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
