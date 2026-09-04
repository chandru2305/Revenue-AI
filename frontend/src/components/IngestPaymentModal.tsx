import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { FailureReason, PaymentMethodType } from "../api/types";
import { Button } from "./ui";

const FAILURE_REASONS: FailureReason[] = [
  "insufficient_funds",
  "expired_instrument",
  "authentication_failed",
  "gateway_timeout",
  "network_error",
  "provider_error",
  "unknown",
];

const METHODS: PaymentMethodType[] = ["card", "upi", "netbanking", "wallet", "emi"];

export function IngestPaymentModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [amountRupees, setAmountRupees] = useState("150");
  const [failureReason, setFailureReason] = useState<FailureReason>("insufficient_funds");
  const [method, setMethod] = useState<PaymentMethodType>("card");
  const [reference, setReference] = useState("");
  const [providerId, setProviderId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await api.ingestPayment({
        amount: Math.round(Number(amountRupees) * 100),
        failure_reason: failureReason,
        payment_method_type: method,
        customer_reference: reference.trim() || null,
        provider_payment_id: providerId.trim() || null,
        auto_create_case: true,
      });
      onDone();
      if (r.deduplicated) {
        setNote(
          `Duplicate event — provider_payment_id "${providerId.trim()}" already ingested. ` +
            `Returned the existing case, no new record created.`,
        );
        setBusy(false);
      } else {
        onClose();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">Ingest a failed payment</div>
        <div className="modal__body">
          <p className="subtle" style={{ margin: 0 }}>
            Simulates a provider-reported failed payment. A recovery case is opened in{" "}
            <span className="cell-mono">discovered</span>, ready to diagnose.
          </p>
          <div className="field field--full">
            <span className="field__label">Amount (₹)</span>
            <input
              className="input"
              type="number"
              min="1"
              value={amountRupees}
              onChange={(e) => setAmountRupees(e.target.value)}
            />
          </div>
          <div className="field field--full">
            <span className="field__label">Failure reason</span>
            <select className="select" value={failureReason} onChange={(e) => setFailureReason(e.target.value as FailureReason)}>
              {FAILURE_REASONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div className="field field--full">
            <span className="field__label">Payment method</span>
            <select className="select" value={method} onChange={(e) => setMethod(e.target.value as PaymentMethodType)}>
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div className="field field--full">
            <span className="field__label">Customer reference (optional)</span>
            <input
              className="input"
              placeholder="cust_ext_123 — reuse to accumulate history"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
          </div>
          <div className="field field--full">
            <span className="field__label">Provider payment ID (optional)</span>
            <input
              className="input"
              placeholder="pay_evt_001 — resend the same id to test idempotency"
              value={providerId}
              onChange={(e) => setProviderId(e.target.value)}
            />
          </div>
          {note && <p className="inline-note" style={{ borderLeftColor: "var(--warn)" }}>{note}</p>}
          {error && <p className="state state--error" style={{ padding: "8px 12px" }}>{error}</p>}
        </div>
        <div className="modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={busy}>
            {busy ? "Ingesting…" : "Ingest payment"}
          </Button>
        </div>
      </div>
    </div>
  );
}
