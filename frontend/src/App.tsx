import { useEffect, useState } from "react";
import { api } from "./api/client";
import type { SystemInfo } from "./api/types";
import { AppShell } from "./components/AppShell";
import { SECTIONS, type Section } from "./components/sections";
import { OverviewPage } from "./pages/OverviewPage";
import { RecoveryCasesPage } from "./pages/RecoveryCasesPage";
import { PolicyDecisionsPage } from "./pages/PolicyDecisionsPage";
import { AuditTrailPage } from "./pages/AuditTrailPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import "./App.css";

function App() {
  const [section, setSection] = useState<Section>(SECTIONS[0]);
  const [apiStatus, setApiStatus] = useState<"ok" | "degraded" | "unknown">("unknown");
  const [system, setSystem] = useState<SystemInfo | null>(null);

  useEffect(() => {
    api
      .getHealth()
      .then((health) => setApiStatus(health.status))
      .catch(() => setApiStatus("degraded"));
    api
      .getSystemInfo()
      .then(setSystem)
      .catch(() => setSystem(null));
  }, []);

  return (
    <AppShell active={section} onSelect={setSection} apiStatus={apiStatus} system={system}>
      {section === "Command Center" && <OverviewPage system={system} />}
      {section === "Recovery Cases" && <RecoveryCasesPage system={system} />}
      {section === "Policy Decisions" && <PolicyDecisionsPage system={system} />}
      {section === "Audit Trail" && <AuditTrailPage system={system} />}
      {section === "Evaluation" && <EvaluationPage />}
    </AppShell>
  );
}

export default App;
