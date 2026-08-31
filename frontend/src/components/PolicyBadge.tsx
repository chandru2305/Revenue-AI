import type { PolicyDecisionType, PolicyReasonCode } from "../api/types";
import { REASON_CODE_LABEL } from "../lib/labels";
import { Badge } from "./ui";

export function PolicyDecisionBadge({ decision }: { decision: PolicyDecisionType | null | undefined }) {
  if (decision === "allow") return <Badge tone="ok" dot>ALLOW</Badge>;
  if (decision === "block") return <Badge tone="danger" dot>BLOCK</Badge>;
  return <Badge>—</Badge>;
}

export function ReasonCodes({ codes }: { codes: PolicyReasonCode[] | string[] }) {
  if (!codes || codes.length === 0) return <span className="subtle">none</span>;
  return (
    <span className="chips">
      {codes.map((code) => (
        <Badge key={code} tone="warn">
          {REASON_CODE_LABEL[code as PolicyReasonCode] ?? code}
        </Badge>
      ))}
    </span>
  );
}
