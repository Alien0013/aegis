// Renders one config setting by its schema type (bool→toggle, enum→select,
// number, list→comma input, string). Modeled on AEGIS AutoField, my own code.

import { Input, Select, Toggle } from "./ui";
import { titleCase } from "../lib/format";

export interface FieldSchema {
  path: string;
  type: string;
  default: unknown;
  enum?: string[];
  label?: string;
  help?: string;
}

function labelOf(f: FieldSchema): string {
  return f.label || titleCase(f.path.split(".").pop() || f.path);
}

export function AutoField({ field, value, onChange }: {
  field: FieldSchema; value: unknown; onChange: (v: unknown) => void;
}) {
  const label = labelOf(field);
  const hint = field.help || field.path;

  // boolean → inline toggle
  if (field.type === "bool" || field.type === "boolean") {
    return (
      <div className="flex items-center justify-between gap-4 py-2">
        <div className="min-w-0">
          <div className="text-sm text-text">{label}</div>
          <div className="font-mono text-[11px] text-faint">{hint}</div>
        </div>
        <Toggle on={!!value} onChange={onChange} />
      </div>
    );
  }

  const Header = (
    <div className="mb-1">
      <div className="text-sm text-text">{label}</div>
      <div className="font-mono text-[11px] text-faint">{hint}</div>
    </div>
  );

  // enum → select
  if (field.enum && field.enum.length) {
    return (
      <div className="py-2">
        {Header}
        <Select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {field.enum.map((o) => <option key={o} value={o}>{o || "(none)"}</option>)}
        </Select>
      </div>
    );
  }

  // number
  if (field.type === "int" || field.type === "float") {
    return (
      <div className="py-2">
        {Header}
        <Input type="number" value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") { onChange(field.type === "int" ? 0 : 0); return; }
            const n = field.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            if (!Number.isNaN(n)) onChange(n);
          }} />
      </div>
    );
  }

  // list → comma-separated
  if (field.type === "list") {
    return (
      <div className="py-2">
        {Header}
        <Input value={Array.isArray(value) ? value.join(", ") : ""}
          placeholder="comma-separated"
          onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
      </div>
    );
  }

  // string + fallback
  return (
    <div className="py-2">
      {Header}
      <Input value={value === null || value === undefined ? "" : String(value)}
        placeholder={field.type === "null" ? "(null)" : ""}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
