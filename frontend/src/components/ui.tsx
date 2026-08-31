import type { ReactNode } from "react";
import { useState } from "react";
import type { Tone } from "../lib/labels";

export function Card({
  title,
  actions,
  children,
  flush,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <div className="card">
      {(title || actions) && (
        <div className="card__head">
          <span className="card__title">{title}</span>
          {actions}
        </div>
      )}
      <div className={flush ? "card__body card__body--flush" : "card__body"}>{children}</div>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "ok" | "danger";
}) {
  const valueClass =
    tone === "ok" ? "stat__value stat__value--ok" : tone === "danger" ? "stat__value stat__value--danger" : "stat__value";
  return (
    <div className="stat">
      <span className="stat__label">{label}</span>
      <span className={valueClass}>{value}</span>
      {hint ? <span className="stat__hint">{hint}</span> : null}
    </div>
  );
}

export function Badge({
  tone = "neutral",
  dot,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  const cls = ["badge", tone !== "neutral" ? `badge--${tone}` : "", dot ? "badge--dot" : ""]
    .filter(Boolean)
    .join(" ");
  return <span className={cls}>{children}</span>;
}

export function Button({
  children,
  onClick,
  variant = "default",
  size,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost";
  size?: "sm";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const cls = [
    "btn",
    variant === "primary" ? "btn--primary" : "",
    variant === "ghost" ? "btn--ghost" : "",
    size === "sm" ? "btn--sm" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={cls} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status">
      <span className="state__title">{label}</span>
      <div style={{ display: "grid", gap: 8, marginTop: 12, maxWidth: 420, marginInline: "auto" }}>
        <div className="skeleton" />
        <div className="skeleton" style={{ width: "80%" }} />
        <div className="skeleton" style={{ width: "60%" }} />
      </div>
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="state state--error" role="alert">
      <span className="state__title">Couldn’t load this data.</span>
      <span className="state__hint">{message}</span>
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state">
      <span className="state__title">{title}</span>
      {hint ? <span className="state__hint">{hint}</span> : null}
      {action ? <div style={{ marginTop: 14 }}>{action}</div> : null}
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

export function Copyable({ text, display }: { text: string; display?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="row-flex" style={{ gap: 4 }}>
      <span className="cell-mono">{display ?? text}</span>
      <button
        type="button"
        className="copy-btn"
        onClick={() => {
          navigator.clipboard?.writeText(text).then(
            () => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            },
            () => undefined,
          );
        }}
      >
        {copied ? "copied" : "copy"}
      </button>
    </span>
  );
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer__head">
          <span className="drawer__title">{title}</span>
          <button type="button" className="drawer__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </aside>
    </>
  );
}
