import { useEffect, useState } from "react";
import { api } from "./api/client";
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

  useEffect(() => {
    api
      .getHealth()
      .then((health) => setApiStatus(health.status))
      .catch(() => setApiStatus("degraded"));
  }, []);

  return (
    <AppShell active={section} onSelect={setSection} apiStatus={apiStatus}>
      {section === "Overview" && <OverviewPage />}
      {section === "Recovery Cases" && <RecoveryCasesPage />}
      {section === "Policy Decisions" && <PolicyDecisionsPage />}
      {section === "Audit Trail" && <AuditTrailPage />}
      {section === "Evaluation" && <EvaluationPage />}
    </AppShell>
  );
}

export default App;
