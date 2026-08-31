# Recovery Case State Machine

Source of truth: [`backend/app/domain/state_machine.py`](../backend/app/domain/state_machine.py)
and [`backend/app/domain/enums.py`](../backend/app/domain/enums.py). This document explains the
*why*; the code is the *what*.

## States

| Status | Meaning | Terminal? |
|---|---|---|
| `DISCOVERED` | A failed payment has been identified as a candidate recovery case. | No |
| `ELIGIBLE` | Passed basic eligibility checks (not already terminal, has a positive amount, etc.). | No |
| `INELIGIBLE` | Failed eligibility checks (e.g. refunded, disputed, below a minimum amount). | **Yes** |
| `DIAGNOSING` | Root-cause analysis of the failure is in progress. | No |
| `RECOMMENDED` | A recovery action has been proposed (by AI reasoning or a deterministic strategy), not yet policy-checked. | No |
| `POLICY_REVIEW` | The recommended action is being evaluated against the deterministic policy engine. | No |
| `APPROVED` | The policy engine allowed the action. Nothing has executed yet. | No |
| `EXECUTING` | The approved action is being carried out — a Payment Link has been (or is being) created via `PaymentProvider` and, once created, the case sits here awaiting a provider-confirmed payment. | No |
| `RECOVERED` | A Razorpay webhook confirmed the customer actually paid in full; revenue was recaptured. Never reached merely because a Payment Link was created. | **Yes** |
| `STOPPED` | Recovery was deliberately halted (policy limit, low confidence, or an explicit `STOP` recommendation). | **Yes** |
| `ESCALATED` | Handed off to a human because automation isn't appropriate (ambiguous cause, high stakes, or repeated failure). | **Yes** |
| `FAILED` | The execution attempt itself failed (e.g. the retried payment failed again). Not terminal — it can loop back for another diagnosis pass, or be stopped/escalated. | No |

## Transitions

```
DISCOVERED   -> ELIGIBLE, INELIGIBLE
ELIGIBLE     -> DIAGNOSING, STOPPED
INELIGIBLE   -> (terminal)
DIAGNOSING   -> RECOMMENDED, ESCALATED
RECOMMENDED  -> POLICY_REVIEW
POLICY_REVIEW -> APPROVED, STOPPED, ESCALATED
APPROVED     -> EXECUTING
EXECUTING    -> RECOVERED, FAILED, ESCALATED
FAILED       -> DIAGNOSING, STOPPED, ESCALATED
RECOVERED    -> (terminal)
STOPPED      -> (terminal)
ESCALATED    -> (terminal)
```

## Design decisions

- **Every transition is enumerated, not inferred.** `ALLOWED_TRANSITIONS` in
  `state_machine.py` is the only place transitions are defined. Services call
  `validate_transition(current, target)` before writing a new status; an
  invalid transition raises `InvalidStateTransitionError` (HTTP 409) rather
  than silently succeeding.
- **`FAILED` is a loop, not a dead end.** A single execution failure
  shouldn't force a case straight to `STOPPED`/`ESCALATED` — it re-enters
  `DIAGNOSING` so the system can decide, with fresh context (updated attempt
  count, elapsed time), whether another attempt is warranted. The policy
  engine's `max_retry_count` and `max_recovery_window_days` are what
  eventually force the loop to end, not the state machine itself.
- **`POLICY_REVIEW` is always between `RECOMMENDED` and `APPROVED`.** There is
  no direct `RECOMMENDED -> APPROVED` edge. This is deliberate: it makes it
  structurally impossible for a recommendation to be acted on without having
  passed through the deterministic policy engine.
- **Only three ways out.** `RECOVERED`, `STOPPED`, and `ESCALATED` (plus the
  early-exit `INELIGIBLE`) are the only terminal states. Every recovery case
  ends in exactly one of: money recovered, recovery deliberately halted, or a
  human took over.
- **`STOP` and `ESCALATE` must always be reachable.** The policy engine
  (`backend/app/domain/policy.py`) gates active recovery actions
  (`RETRY_PAYMENT`, `SEND_PAYMENT_LINK`, `SEND_REMINDER`) by confidence and
  recovery-window checks, but never blocks `STOP` or `ESCALATE` on those same
  grounds — a case that has gone stale must still be stoppable or escalatable,
  not left in limbo. See `backend/tests/test_policy_engine.py::test_stop_and_escalate_are_never_blocked_by_expired_recovery_window`
  for the regression test.

## What each phase implements

Phase 1 defined and unit-tested the state machine itself (valid/invalid
transitions, terminal-state detection) with no service walking a case
through it end to end. Phase 2 wired `DISCOVERED -> ... -> APPROVED/
STOPPED/ESCALATED` via AI diagnosis + the policy engine
(`POST .../diagnose`), but still executed nothing. **Phase 3 wires the
rest**: `POST .../execute` walks `APPROVED -> EXECUTING`, and a
Razorpay webhook (never the execute call itself) walks
`EXECUTING -> RECOVERED`/`FAILED`. No new states or transitions were
added for this — `EXECUTING` (defined in Phase 1) turned out to already
cover both "provider call in flight" and "Payment Link created, awaiting
payment"; see `docs/razorpay-integration.md` "Execution state machine"
for why an `AWAITING_PAYMENT` state was deliberately not added. The
`evaluation/` package continues to exercise only the underlying
*decision* logic (diagnosis + recommended action + safety checks)
against synthetic data, independent of this state machine and of the
real execution pipeline — see `docs/evaluation-methodology.md` and
`docs/razorpay-integration.md` "Simulated vs. real evaluation" for why
those two are deliberately never combined into one number.
