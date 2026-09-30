import { type ButtonHTMLAttributes, type KeyboardEvent, type ReactNode, useId, useRef,
         useState } from "react";

/** A button: primary (one per view), secondary (default) or ghost. None of them control
 * equipment: this system has no control actions. */
export function Button({ variant = "secondary", size, className = "", ...rest }:
                       ButtonHTMLAttributes<HTMLButtonElement> &
                       { variant?: "primary" | "secondary" | "ghost"; size?: "sm" }) {
  const cls = [variant !== "secondary" && `btn-${variant}`, size && `btn-${size}`, className]
    .filter(Boolean).join(" ");
  return <button type="button" className={cls || undefined} {...rest} />;
}

export interface Tab { id: string; label: string; content: ReactNode }

/** Tabs with the ARIA tab pattern: arrow keys, Home and End move between tabs. */
export function Tabs({ tabs, label, initial }: { tabs: Tab[]; label: string; initial?: string }) {
  const [active, setActive] = useState(initial ?? tabs[0]?.id);
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (e: KeyboardEvent, i: number) => {
    const n = tabs.length;
    const j = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n
      : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (j < 0) return;
    e.preventDefault();
    setActive(tabs[j].id);
    refs.current[j]?.focus();
  };
  return (
    <div>
      <div role="tablist" aria-label={label} className="tablist">
        {tabs.map((t, i) => (
          <button key={t.id} ref={(el) => { refs.current[i] = el; }} role="tab" type="button"
                  id={`${base}-tab-${t.id}`} aria-controls={`${base}-panel-${t.id}`}
                  aria-selected={t.id === active} tabIndex={t.id === active ? 0 : -1}
                  className="tab" onClick={() => setActive(t.id)}
                  onKeyDown={(e) => move(e, i)}>{t.label}</button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} role="tabpanel" id={`${base}-panel-${t.id}`}
             aria-labelledby={`${base}-tab-${t.id}`} hidden={t.id !== active}
             className="tabpanel" tabIndex={0}>{t.id === active && t.content}</div>
      ))}
    </div>
  );
}

interface FieldProps { label: string; hint?: string; error?: string }

function FieldShell({ label, hint, error, id, children }:
                    FieldProps & { id: string; children: ReactNode }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}</label>
      {children}
      {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}
      {error && <span className="field-error" id={`${id}-error`} role="status">{error}</span>}
    </div>
  );
}

const described = (id: string, { hint, error }: FieldProps) =>
  [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;

export function TextField({ label, hint, error, value, onChange, placeholder, mono }:
                          FieldProps & { value: string; onChange: (v: string) => void;
                                         placeholder?: string; mono?: boolean }) {
  const id = useId();
  return (
    <FieldShell {...{ label, hint, error, id }}>
      <input id={id} value={value} placeholder={placeholder} className={mono ? "mono" : undefined}
             aria-invalid={error ? true : undefined} aria-describedby={described(id, { label, hint, error })}
             onChange={(e) => onChange(e.target.value)} />
    </FieldShell>
  );
}

export function SelectField({ label, hint, error, value, onChange, options }:
                            FieldProps & { value: string; onChange: (v: string) => void;
                                           options: { value: string; label: string }[] }) {
  const id = useId();
  return (
    <FieldShell {...{ label, hint, error, id }}>
      <select id={id} value={value} aria-invalid={error ? true : undefined}
              aria-describedby={described(id, { label, hint, error })}
              onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </FieldShell>
  );
}

export function TextAreaField({ label, hint, error, value, onChange, rows = 3 }:
                              FieldProps & { value: string; onChange: (v: string) => void;
                                             rows?: number }) {
  const id = useId();
  return (
    <FieldShell {...{ label, hint, error, id }}>
      <textarea id={id} rows={rows} value={value} aria-invalid={error ? true : undefined}
                aria-describedby={described(id, { label, hint, error })}
                onChange={(e) => onChange(e.target.value)} />
    </FieldShell>
  );
}
