// AEGIS design-system primitives — small, themeable building blocks used by
// every page. All colors come from theme CSS variables via Tailwind tokens.

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useEffect, useState } from "react";
import { cn } from "../lib/cn";
import { Icon } from "./icons";

/* ── Button ─────────────────────────────────────────────────────────── */
type ButtonVariant = "primary" | "ghost" | "outline" | "danger";
const BTN: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-fg hover:bg-primary/90 border border-primary",
  ghost: "bg-transparent text-dim hover:bg-surface-2 hover:text-text border border-transparent",
  outline: "bg-transparent text-text hover:bg-surface-2 border border-border",
  danger: "bg-transparent text-danger hover:bg-danger/10 border border-danger/45",
};
export function Button({
  variant = "outline", icon, sm, className, children, ...rest
}: {
  variant?: ButtonVariant; icon?: string; sm?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn(
        "inline-flex min-h-8 items-center justify-center gap-1.5 rounded-[var(--radius)] font-mono font-semibold",
        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none",
        sm ? "px-2.5 py-1 text-[11px]" : "px-3.5 py-1.5 text-xs",
        BTN[variant], className,
      )}
      {...rest}
    >
      {icon && <Icon name={icon} size={sm ? 13 : 15} />}
      {children}
    </button>
  );
}

/* ── Card ───────────────────────────────────────────────────────────── */
export function Card({
  title, sub, actions, pad = true, className, children,
}: {
  title?: ReactNode; sub?: ReactNode; actions?: ReactNode;
  pad?: boolean; className?: string; children?: ReactNode;
}) {
  return (
    <section className={cn(
      "rounded-[var(--radius)] border border-border bg-surface/72 backdrop-blur-[1px]", className,
    )}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2.5">
          <div className="min-w-0">
            {title && <h2 className="truncate font-mono text-sm font-semibold text-text">{title}</h2>}
            {sub && <p className="truncate text-xs text-faint">{sub}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn(pad && "p-[var(--pad)]")}>{children}</div>
    </section>
  );
}

/* ── Badge ──────────────────────────────────────────────────────────── */
type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";
const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-dim border-border",
  primary: "bg-primary/15 text-primary border-primary/30",
  success: "bg-success/15 text-success border-success/30",
  warning: "bg-warning/15 text-warning border-warning/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  info: "bg-info/15 text-info border-info/30",
};
const STATUS_TONE: Record<string, Tone> = {
  ok: "success", done: "success", ready: "success", active: "success", running: "info",
  in_progress: "info", pending: "warning", blocked: "warning", warn: "warning",
  error: "danger", failed: "danger", denied: "danger",
};
export function Badge({ children, tone, status }: { children?: ReactNode; tone?: Tone; status?: string }) {
  const t: Tone = tone ?? (status ? STATUS_TONE[status.toLowerCase()] ?? "neutral" : "neutral");
  return (
    <span className={cn(
      "inline-flex items-center gap-1 rounded-[var(--radius)] border px-2 py-0.5 font-mono text-[10px] font-medium",
      TONE[t],
    )}>
      {status && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children ?? status}
    </span>
  );
}

/* ── Inputs ─────────────────────────────────────────────────────────── */
const FIELD = "w-full rounded-[var(--radius)] border border-border bg-surface-2/80 px-3 py-1.5 " +
  "text-sm text-text placeholder:text-faint outline-none focus:border-primary/60 " +
  "focus:ring-2 focus:ring-primary/20 transition-colors";
export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(FIELD, props.className)} />;
}
export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(FIELD, "cursor-pointer", className)} {...rest}>{children}</select>;
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="font-mono text-[10px] font-medium uppercase tracking-wide text-dim">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

/* ── Page header ────────────────────────────────────────────────────── */
export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-[var(--gap)] flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
      <div className="min-w-0">
        <h1 className="font-mono text-xl font-semibold text-text">{title}</h1>
        {sub && <p className="mt-0.5 text-xs text-dim">{sub}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ── States ─────────────────────────────────────────────────────────── */
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block rounded-full border-2 border-border border-t-primary"
      style={{ width: size, height: size, animation: "spin 0.7s linear infinite" }}
    />
  );
}
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-dim">
      <Spinner /> {label}
    </div>
  );
}
export function Empty({ children, icon }: { children: ReactNode; icon?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-sm text-faint">
      {icon && <Icon name={icon} size={28} />}
      <span>{children}</span>
    </div>
  );
}

/* ── Stat tile ──────────────────────────────────────────────────────── */
// Literal classes (not `text-${tone}`) so Tailwind's JIT scanner emits them.
const ICON_TONE: Record<Tone, string> = {
  neutral: "text-faint", primary: "text-primary", success: "text-success",
  warning: "text-warning", danger: "text-danger", info: "text-info",
};
export function Stat({ label, value, icon, tone }: { label: string; value: ReactNode; icon?: string; tone?: Tone }) {
  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface/72 p-[var(--pad)]">
      <div className="flex items-center justify-between text-faint">
        <span className="font-mono text-[10px] font-medium uppercase tracking-wide">{label}</span>
        {icon && <Icon name={icon} size={15} className={ICON_TONE[tone ?? "neutral"]} />}
      </div>
      <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-text">{value}</div>
    </div>
  );
}

/* ── Toggle ─────────────────────────────────────────────────────────── */
export function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button" role="switch" aria-checked={on} disabled={disabled}
      onClick={() => onChange(!on)}
      className={cn("relative h-5 w-9 shrink-0 rounded-[var(--radius)] border border-border transition-colors disabled:opacity-40",
        on ? "bg-primary" : "bg-border-2")}
    >
      <span className={cn("absolute top-0.5 h-4 w-4 rounded-[calc(var(--radius)-1px)] bg-primary-fg transition-transform",
        on ? "left-0.5 translate-x-4" : "left-0.5")} />
    </button>
  );
}

export function Segmented<T extends string>({ value, onChange, items, className }: {
  value: T;
  onChange: (value: T) => void;
  items: Array<{ value: T; label: ReactNode; icon?: string; count?: ReactNode }>;
  className?: string;
}) {
  return (
    <div className={cn("inline-flex flex-wrap border border-border bg-surface/70 p-0.5", className)}>
      {items.map((item) => (
        <button
          key={item.value}
          onClick={() => onChange(item.value)}
          className={cn(
            "inline-flex min-h-8 items-center gap-1.5 px-3 py-1 font-mono text-xs transition",
            value === item.value ? "bg-primary text-primary-fg" : "text-dim hover:bg-surface-2 hover:text-text",
          )}
        >
          {item.icon && <Icon name={item.icon} size={13} />}
          {item.label}
          {item.count !== undefined && <span className="text-[10px] opacity-75">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SectionTitle({ icon, title, sub, actions }: {
  icon?: string;
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 font-mono text-base font-semibold text-text">
          {icon && <Icon name={icon} size={16} className="text-primary" />}
          <span className="truncate">{title}</span>
        </div>
        {sub && <div className="mt-0.5 truncate text-xs text-faint">{sub}</div>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function MetricStrip({ items }: { items: Array<{ label: string; value: ReactNode; tone?: Tone }> }) {
  return (
    <div className="grid gap-px border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="bg-surface/78 px-[var(--pad)] py-3">
          <div className="font-mono text-xl font-semibold tabular-nums text-text">{item.value}</div>
          <div className={cn(
            "mt-0.5 font-mono text-[10px] uppercase tracking-wide",
            item.tone ? TONE[item.tone].split(" ").find((x) => x.startsWith("text-")) : "text-faint",
          )}>{item.label}</div>
        </div>
      ))}
    </div>
  );
}

/* ── Toasts ─────────────────────────────────────────────────────────── */
type ToastKind = "ok" | "err" | "info";
export function toast(message: string, kind: ToastKind = "ok"): void {
  window.dispatchEvent(new CustomEvent("aegis-toast", { detail: { message, kind } }));
}
const TOAST_TONE: Record<ToastKind, string> = {
  ok: "border-success/40 bg-success/15 text-success",
  err: "border-danger/40 bg-danger/15 text-danger",
  info: "border-info/40 bg-info/15 text-info",
};
export function Toaster() {
  const [items, setItems] = useState<{ id: number; message: string; kind: ToastKind }[]>([]);
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail as { message: string; kind: ToastKind };
      const id = Date.now() + Math.random();
      setItems((x) => [...x, { id, ...d }]);
      setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 3500);
    };
    window.addEventListener("aegis-toast", h);
    return () => window.removeEventListener("aegis-toast", h);
  }, []);
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
      {items.map((i) => (
        <div key={i.id} className={cn("animate-fade-in rounded-[var(--radius)] border px-3.5 py-2 text-sm shadow-lg", TOAST_TONE[i.kind])}>
          {i.message}
        </div>
      ))}
    </div>
  );
}
