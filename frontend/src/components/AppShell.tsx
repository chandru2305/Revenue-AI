import type { ReactNode } from "react";
import { SECTION_CRUMB, SECTION_GROUPS, type Section } from "./sections";

type ApiStatus = "ok" | "degraded" | "unknown";

export function AppShell({
  active,
  onSelect,
  apiStatus,
  actions,
  children,
}: {
  active: Section;
  onSelect: (s: Section) => void;
  apiStatus: ApiStatus;
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
            <span className="sidebar__sub">Revenue recovery control</span>
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
          AI reasons · deterministic systems enforce · webhooks confirm · every step audited.
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
            <span className="env-pill">Test Mode</span>
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
