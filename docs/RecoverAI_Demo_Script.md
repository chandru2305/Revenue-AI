# RecoverAI — 5-Minute Demo Video Script
### Razorpay Buildathon, Track 03: AI Revenue Recovery

**Total runtime target: 5:00.** Timestamps are cumulative — if you run long in one section, trim the next. Narration lines are written to be spoken, not read stiffly — adjust to your voice.

---

## 0:00–0:25 — Hook: the problem

**[SCREEN: title card or your face on camera]**

> "Failed payments quietly leak revenue. A card gets declined, a payment times out, insufficient funds — and most teams either chase it manually, one ticket at a time, or don't chase it at all. That money is usually recoverable. It's just nobody's job to prove it, safely, at scale.
>
> That's the problem RecoverAI solves."

---

## 0:25–0:55 — What RecoverAI is (one-line pitch)

**[SCREEN: architecture diagram or README title]**

> "RecoverAI is a bounded, auditable revenue-recovery pipeline. It finds at-risk failed payments, uses AI to diagnose *why* each one failed and recommend a recovery action, checks that recommendation against a deterministic policy engine, executes only what's permitted through Razorpay, and logs every decision for audit.
>
> The one sentence that matters most: **the AI reasons, but deterministic code enforces.** The model never gets to move money on its own."

---

## 0:55–1:40 — The core design principle (this is your differentiator — spend real time here)

**[SCREEN: `docs/architecture.md` or the state-machine diagram, or just narrate over the workflow diagram]**

> "Here's the flow. A failed payment comes in. An LLM — we're running Groq's `openai/gpt-oss-120b` — reads the payment and customer context and produces a diagnosis: root cause, a recommended action, a confidence score.
>
> That recommendation then hits a pure, dependency-free policy engine. No LLM call, no network, just deterministic code enforcing retry limits, recovery windows, contact caps, amount bounds, and action eligibility. If it fails policy, the case stops or escalates to a human — nothing executes. If it passes, we create a real Razorpay Test Mode Payment Link, and only a signature-verified webhook confirming full payment marks the case 'Recovered.'
>
> This also means the system is genuinely agentic — not because the model has tools, but because the *orchestrator* runs an autonomous discover-diagnose-decide-act loop on a schedule. The agency lives in the orchestration. The model makes one structured judgment call per case and stops there. That split is deliberate: giving the LLM the power to create payment links or contact customers directly would move enforcement inside a model we can't fully trust. The policy engine exists specifically to prevent that."

---

## 1:40–3:30 — Live dashboard walkthrough (the longest section — this is the proof)

**[SCREEN: switch to the running app at localhost:8080 or wherever you have it deployed]**

Suggested path through the UI — narrate as you click:

**Command Center tab (≈20s)**
> "This is the operator dashboard's Command Center. The panel at the top is the autonomous agent — its discover → diagnose → decide → act → observe loop, the last cycle's counts, and a live activity feed. Below it, live recovery posture — eligible revenue at risk, confirmed recovered revenue, recovery rate, computed only from webhook-confirmed payments. On a fresh database every number is honestly zero. Nothing here is invented."

**Seed demo batch (≈40s)**
> "Since this is a fresh environment, let me seed a demo batch — thirty realistic failed-payment cases through the real pipeline: live AI diagnosis, the real policy engine, real state transitions."
**[Click "Seed demo batch (dev)" and let it execute on screen — takes ~2 min for 30 live Groq calls]**
> "And there it is — a measured recovery rate across the batch, with a mix of outcomes: some recovered, some expired, some stopped by policy, some escalated. That mix is deliberate — a suspiciously perfect 100% wouldn't demonstrate that the stopping rules actually work."

**Recovery Cases + case drawer (≈45s)**
> "Here's the case list, filterable by status. Let me open one."
**[Click into a case]**
> "This drawer shows the full story for one payment: the AI's diagnosis and rationale, the policy engine's ALLOW or BLOCK decision with a reason code, and — if it was permitted — the execution and webhook confirmation. Every one of these arrows is a real, tested code path, not a mockup."

**Policy Decisions tab (≈25s)**
> "This page is every policy decision the system has made — every ALLOW and BLOCK, with the reason code and policy version attached. This is what makes 'compliant escalation' provable rather than just claimed."

**Audit Trail tab (≈20s)**
> "And this is the append-only audit log — every state transition, correlation-ID keyed, including the autonomous orchestrator cycles themselves, recorded as a machine actor. You can filter by entity, event type, or correlation ID and replay exactly what happened and why."

---

## 3:30–4:15 — The honest part: AI evaluation results

**[SCREEN: the Evaluation tab, or the comparison table in the README]**

> "Now the part most demos skip: does the AI actually help? We ran a real head-to-head — thirty held-out cases, live Groq calls, zero provider failures.
>
> The rule-based baseline scored an 0.87 intervention accuracy. The LLM scored 0.47 — worse, on every metric we measured.
>
> We're not hiding that. Two things bound what it means: the ground truth was authored from the same heuristics the baseline implements, so this measures 'does the AI reproduce our rules,' not 'does the AI recover more money.' And the benchmark encodes failure reasons as a fixed enum, so it never exercises the messy free-text case the AI was actually added for.
>
> What this run *did* prove: the integration works end to end, and it exposed two real defects — a retired model pin and a timeout below observed latency — that a fake test double could never have caught. On the evidence we have today, the deterministic baseline is the safer recommender. We're saying that plainly because the policy engine, not the model, is what makes this system trustworthy either way."

---

## 4:15–4:45 — Meeting the Track 03 bar (recap, fast)

**[SCREEN: could be a simple checklist slide, or just talk over the dashboard]**

> "Against the brief — measured money recovered across a batch, compliant escalation, stopping rules, and an audit trail — here's where each one lives: the recovery batch endpoint for measured revenue, the policy engine's BLOCK path for escalation, the retry caps and recovery windows for stopping rules, and the append-only audit log with full timeline replay for the trail. The one gap we're upfront about: Razorpay Test Mode credentials weren't available during this build, so the payment-link write and webhook confirmation are simulated by a fake provider standing in for that one external call — the recovery-rate math itself is real, over real database rows."

---

## 4:45–5:00 — Close

**[SCREEN: back to camera or a closing slide]**

> "RecoverAI: AI diagnoses, deterministic policy decides, and every action is bounded and auditable. That's RecoverAI — thanks for watching."

---

## Recording notes

- **Have the app already running** (`make up`) and a browser tab open before you hit record, so you're not waiting on Docker on camera. Pre-seed one batch beforehand as a fallback in case the live run is slow, but still show the button being clicked live.
- **Pace check:** read the whole script aloud once with a timer before recording — this draft runs close to 5:00 at a natural pace (~140–150 words/min), but trim the walkthrough section first if you're over, since it's the most flexible.
- **Screen resolution:** record at 1920×1080, and zoom your browser to ~110–125% so dashboard text is legible on smaller viewers' screens (this is a common hackathon-demo failure point).
- **Cut the honesty section only if desperate for time** — it's unusual for a hackathon demo to admit a negative result, and it's exactly the kind of thing that makes judges trust the rest of the claims. Keep it if you can.
- **Optional B-roll**: a quick cut to `policy.py` or the state-machine diagram in `docs/recovery-state-machine.md` while you talk over 0:55–1:40 reinforces "deterministic, not vibes."
