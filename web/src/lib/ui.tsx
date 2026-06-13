// Shared UI kit — one vocabulary of primitives every page builds on, so the whole
// dashboard stays consistent. Pure presentation; all data logic lives in the pages.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "./icons";

/* ---------- page header ---------- */
export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="head">
      <div><h1>{title}</h1>{sub != null && <span className="crumb">{sub}</span>}</div>
      {actions && <span className="actions">{actions}</span>}
    </div>
  );
}

/* ---------- card ---------- */
export function Card({ title, actions, children, pad = true, className = "" }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean; className?: string }) {
  if (!title && !actions) return <div className={`card ${pad ? "" : "pad0"} ${className}`}>{children}</div>;
  return (
    <div className={`card pad0 ${className}`}>
      <div className="card-h"><h3>{title}</h3>{actions && <span className="actions">{actions}</span>}</div>
      <div className={pad ? "card-b" : ""}>{children}</div>
    </div>
  );
}

/* ---------- button ---------- */
export function Button({ children, onClick, variant = "primary", sm, disabled, icon, type }:
  { children?: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger"; sm?: boolean; disabled?: boolean; icon?: string; type?: "button" | "submit" }) {
  return (
    <button type={type || "button"} className={`btn ${variant === "primary" ? "" : variant} ${sm ? "sm" : ""}`} onClick={onClick} disabled={disabled}>
      {icon && <span style={{ display: "inline-flex", width: 14, height: 14 }}><Icon n={icon} /></span>}{children}
    </button>
  );
}

/* ---------- badge from a status string ---------- */
export function Badge({ status, children }: { status?: string; children?: ReactNode }) {
  const s = String(status || "").toLowerCase();
  let cls = "";
  if (/(^|[^a-z])(ok|done|ready|set|completed|enabled|active|approved|pass)/.test(s)) cls = "ok";
  else if (/(run|busy|progress|working|pending|connecting|live)/.test(s)) cls = "run";
  else if (/(err|fail|deny|denied|offline|blocked)/.test(s)) cls = "err";
  else if (/(warn|paused|disabled|idle|queue)/.test(s)) cls = "warn";
  return <span className={`badge dot ${cls}`}>{children ?? status}</span>;
}

/* ---------- stat tile ---------- */
export function Stat({ label, value, sub, onClick }: { label: string; value: ReactNode; sub?: ReactNode; onClick?: () => void }) {
  return (
    <div className={`card stat ${onClick ? "click" : ""}`} onClick={onClick}>
      <div className="lbl">{label}</div>
      <div className="val">{value}</div>
      {sub != null && <div className="sub">{sub}</div>}
    </div>
  );
}

/* ---------- labelled field ---------- */
export function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <label className="lbl">{label}{children}</label>;
}

/* ---------- toggle switch ---------- */
export function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <span className="switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
    </span>
  );
}

/* ---------- empty / loading / error states ---------- */
export function Empty({ children, small }: { children: ReactNode; small?: boolean }) {
  return <div className={`empty ${small ? "small" : ""}`}>{children}</div>;
}
export function Loading() { return <div className="empty"><span className="spin" /> loading…</div>; }
export function Spinner({ sm }: { sm?: boolean }) { return <span className={`spin ${sm ? "sm" : ""}`} />; }

/* ---------- toast feedback ---------- */
type ToastFn = (msg: string, kind?: "ok" | "err" | "info") => void;
const ToastCtx = createContext<ToastFn>(() => {});
export function useToast() { return useContext(ToastCtx); }
export function ToastHost({ children }: { children: ReactNode }) {
  const [t, setT] = useState<{ msg: string; kind: string } | null>(null);
  const timer = useRef<any>(null);
  const push = useCallback<ToastFn>((msg, kind = "info") => {
    setT({ msg, kind });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setT(null), 2600);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {t && <div className={`toast ${t.kind}`}>{t.msg}</div>}
    </ToastCtx.Provider>
  );
}

/* ---------- toolbar with search ---------- */
export function Toolbar({ q, setQ, placeholder, children }:
  { q?: string; setQ?: (v: string) => void; placeholder?: string; children?: ReactNode }) {
  return (
    <div className="toolbar">
      {setQ && <input className="search" placeholder={placeholder || "Filter…"} value={q} onChange={(e) => setQ(e.target.value)} />}
      {children}
    </div>
  );
}
