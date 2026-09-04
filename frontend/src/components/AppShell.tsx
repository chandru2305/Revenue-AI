import type { ReactNode } from "react";
import type { SystemInfo } from "../api/types";
import { SECTION_CRUMB, SECTION_GROUPS, type Section } from "./sections";

type ApiStatus = "ok" | "degraded" | "unknown";

function ProviderPill({ system }: { system: SystemInfo | null }) {
  if (!system) return <span className="env-pill">…</span>;
  if (system.demo_mode) {
    return (
      <span
        className="mode-pill mode-pill--demo"
        title="Payment confirmation is simulated (no Razorpay Test Mode key). Orchestration, policy enforcement, webhook processing, and audit logic use the real application pipeline."
      >
        <span className="mode-pill__dot" />
        Demo mode — payment simulated
      </span>
    );
  }
  return (
    <span
      className="mode-pill mode-pill--live"
      title={`Razorpay ${system.payment_provider_mode} mode`}
    >
      <span className="mode-pill__dot" />
      Razorpay {system.payment_provider_mode}
    </span>
  );
}

export function AppShell({
  active,
  onSelect,
  apiStatus,
  system,
  actions,
  children,
}: {
  active: Section;
  onSelect: (s: Section) => void;
  apiStatus: ApiStatus;
  system?: SystemInfo | null;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="sidebar__brand">
          <span className="sidebar__mark">R</span>
          <span>
            <span className="sidebar__name">RecoverAI</span>
            <span className="sidebar__sub">Autonomous recovery agent</span>
          </span>
        </div>

        {SECTION_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="sidebar__group-label">{group.label}</div>
            {group.items.map((item) => (
              <button
                key={item}
                type="button"
                className={item === active ? "nav-item nav-item--active" : "nav-item"}
                onClick={() => onSelect(item)}
              >
                <span className="nav-item__dot" />
                {item}
              </button>
            ))}
          </div>
        ))}

        <div className="sidebar__foot">
          <span className="sidebar__principle">
            <b>AI reasons.</b> Policy decides. The agent acts. Webhooks verify.
          </span>
          Every step is recorded in an append-only audit trail.
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div>
            <div className="topbar__title">{active}</div>
            <div className="topbar__crumb">{SECTION_CRUMB[active]}</div>
          </div>
          <div className="topbar__right">
            {actions}
            <ProviderPill system={system ?? null} />
            <span className={`api-status api-status--${apiStatus}`}>
              <span className="api-status__dot" />
              API {apiStatus}
            </span>
          </div>
        </header>

        <div className="content">{children}</div>
      </div>
    </div>
  );
}
