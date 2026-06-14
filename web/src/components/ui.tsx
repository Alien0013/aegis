// AEGIS design-system primitives — small, themeable building blocks used by
// every page. All colors come from theme CSS variables via Tailwind tokens.

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useEffect, useState } from "react";
import { cn } from "../lib/cn";
import { Icon } from "./icons";

/* ── Button ─────────────────────────────────────────────────────────── */
type ButtonVariant = "primary" | "ghost" | "outline" | "danger";
const BTN: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-fg hover:opacity-90 border border-transparent",
  ghost: "bg-transparent text-dim hover:bg-surface-2 hover:text-text border border-transparent",
  outline: "bg-surface text-text hover:bg-surface-2 border border-border",
  danger: "bg-transparent text-danger hover:bg-danger/10 border border-danger/40",
};
export function Button({
  variant = "outline", icon, sm, className, children, ...rest
}: {
  variant?: ButtonVariant; icon?: string; sm?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[var(--radius)] font-medium",
        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none",
        sm ? "px-2.5 py-1 text-xs" : "px-3.5 py-1.5 text-sm",
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
      "rounded-[calc(var(--radius)+2px)] border border-border bg-surface", className,
    )}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-[var(--pad)] py-2.5">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-text">{title}</h2>}
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
      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
      TONE[t],
    )}>
      {status && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children ?? status}
    </span>
  );
}

/* ── Inputs ─────────────────────────────────────────────────────────── */
const FIELD = "w-full rounded-[var(--radius)] border border-border bg-surface-2 px-3 py-1.5 " +
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
      <span className="text-xs font-medium text-dim">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

/* ── Page header ────────────────────────────────────────────────────── */
export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-[var(--gap)] flex items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-text">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-dim">{sub}</p>}
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
    <div className="rounded-[calc(var(--radius)+2px)] border border-border bg-surface p-[var(--pad)]">
      <div className="flex items-center justify-between text-faint">
        <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
        {icon && <Icon name={icon} size={15} className={ICON_TONE[tone ?? "neutral"]} />}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-text">{value}</div>
    </div>
  );
}

/* ── Toggle ─────────────────────────────────────────────────────────── */
export function Toggle({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button" role="switch" aria-checked={on} disabled={disabled}
      onClick={() => onChange(!on)}
      className={cn("relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40",
        on ? "bg-primary" : "bg-border-2")}
    >
      <span className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform",
        on ? "left-0.5 translate-x-4" : "left-0.5")} />
    </button>
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
