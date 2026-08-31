export const SECTIONS = [
  "Overview",
  "Recovery Cases",
  "Policy Decisions",
  "Audit Trail",
  "Evaluation",
] as const;

export type Section = (typeof SECTIONS)[number];

export const SECTION_GROUPS: { label: string; items: Section[] }[] = [
  { label: "Operations", items: ["Overview", "Recovery Cases"] },
  { label: "Assurance", items: ["Policy Decisions", "Audit Trail"] },
  { label: "Analysis", items: ["Evaluation"] },
];

export const SECTION_CRUMB: Record<Section, string> = {
  Overview: "Live recovery posture for this deployment",
  "Recovery Cases": "Every failed-payment recovery opportunity",
  "Policy Decisions": "Deterministic ALLOW / BLOCK gate activity",
  "Audit Trail": "Append-only record of every decision and action",
  Evaluation: "Synthetic-dataset decision-quality run",
};
