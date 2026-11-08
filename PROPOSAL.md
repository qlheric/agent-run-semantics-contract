# Agent Run Semantics Contract v0.4.0 (proposal, for review)

> **Status**: v0.4.0-draft, seeking **at least 2 external technical reviews**. The three hard defects from review 01 are fixed; version history in [`CHANGELOG.md`](CHANGELOG.md).
> **What changed since review 01**: transition-table authority is now real (both implementations read it), audit hashes are computed per RFC 8785 JCS (all fixtures recomputed), `reserve → settle` and `step[]` are in the schema, `outcome_unknown → running` is removed and blacklisted, and a new **`--replay`** check ties the audit chain to the state machine.
> **The claim we now consider checkable**: *an audit chain that replays into a legal state sequence, and refuses to leave `outcome_unknown` without evidence.*
> Response channel: open an issue with `[run-semantics]` (template provided) or comment on #1.
> Every external review will be listed by name in this file with accept/reject decisions.

## 0. Short version

A **reliability contract for one run**: states, legal transitions, idempotency scope, integer micro-USD budget (`reserve → settle`), JCS-hashed audit chain, capability-tiered tool exposure, and a first-class `outcome_unknown` state that **cannot silently re-enter execution**. It is expressed as a JSON Schema + fixtures + a cross-language conformance protocol, and has been implemented independently twice (Java control plane, Python translation layer) with **byte-identical** validation output.

**We do not claim to be first.** Several adjacent standards exist (see §2). Our bet is narrower: these are *contract-level* decisions that protocols leave to implementations, and they are the ones that decide whether "retry" is safe.

## 0.1 The one property we are willing to call novel

`--replay` takes the audit chain (hash-chained, JCS-canonical) and replays every `state_transition` entry against the **authoritative** transition table:

1. the first `from` must be `created`;
2. each `from` must equal the previous entry's `to`;
3. every edge must be legal in `status-transitions.json`;
4. leaving `outcome_unknown` for a terminal state requires `payload.evidence_ref` (reconciliation evidence).

Consequence: **tampering with any state transition breaks both the hash chain and the replay check.** A chain that merely appends hashes cannot make that statement. We believe this combination (replayable audit ⇒ legal state sequence) is not present in A2A, MCP Tasks, or OTel GenAI.

## 1. Contract shape (v0.4.0)

- A run document = `{spec, run, step[]}`; `additionalProperties: false` at every level, with an `extensions`/`x-` escape hatch for interoperable extension fields.
- **51 exported fields** (`python tools/contract_check.py --fields --count`); clone-and-run validator in `tools/contract_check.py`.

| Area | Decision |
|---|---|
| States | 9: `created / running / waiting_approval / paused / degraded / succeeded / failed / cancelled / outcome_unknown` |
| Transitions | **single authority = `contract/status-transitions.json`** (schema only defines shape; fixture tables are examples) |
| Budget | **integer micro-USD**, floats rejected; `reserve → settle`; retries never reserve twice; difference refunded |
| Idempotency | `step.idempotency_scope = run_id/step_id/tool`; `step.attempt` + `policy.max_attempts` |
| Audit | append-only hash chain: `sha256(seq|ts|type|run_id|step_id|attempt|canonical(payload)|prev_hash)`, genesis `prev_hash` = 64 zeros |
| Tools | catalog filtered by `policy.capability_tier` (`read/write/destructive`), approval gate (PEP) outside the model |
| Ambiguity | `outcome_unknown`: side effect may have happened ⇒ **automatic retry forbidden**; resolve via ledger/reconciliation |
| Canonical JSON | keys by code point, arrays preserved, null keys dropped, NFC, UTF-8 unescaped, floats always rejected |

## 2. Related work & differences (new in v0.2 — the main thing we want reviewed)

We deliberately compare against the nearest work instead of asserting novelty.

| Work | What it already covers | What this proposal adds / differs | Open question for reviewers |
|---|---|---|---|
| **A2A Protocol v1.0.0** ([spec](https://a2a-protocol.org/latest/specification/)) | Inter-agent protocol: Task data model, task states, idempotency semantics (§3.3.1), streaming & async delivery, task listing/cancel; terminal tasks cannot restart | A2A is a **messaging/interop** standard between agents; it intentionally leaves budget accounting, audit-chain format, and tool-capability tiers to implementations. We add a *control-plane ⇄ execution-plane* contract for those, plus a canonical transition-table artifact | Should this contract be re-expressed as an **A2A profile** (map our 9 states onto A2A TaskState + budget/audit extensions) instead of a standalone schema? |
| **MCP Tasks extension** (protocol `2026-07-28`, `io.modelcontextprotocol/tasks`; [overview](https://modelcontextprotocol.io/extensions/tasks/overview)) | Long-running operation lifecycle: `working / input_required / completed / failed / cancelled`, durable task handle, `tasks/get|update|cancel`, TTL + poll interval; task-augmented calls are not retried while inline calls may retry on transport error | MCP Tasks is a **transport/session** lifecycle. We specify run-level semantics *around* it: budget reservation, audit chain, idempotency scope, and explicit `outcome_unknown` semantics | Should our state names align to MCP task statuses (`input_required` vs `waiting_approval`), and should "task calls are never retried" be a rule in this contract? |
| **OpenTelemetry GenAI semantic conventions** (in Development as of 2026; `gen_ai.*`, `invoke_agent` / `chat` / `execute_tool` spans) | Observability: span naming and attributes for agent and tool calls | Telemetry ≠ semantics: OTel can describe a retry but cannot forbid it. We keep the contract separate and expose an audit payload that can be projected into spans | Are our audit entry fields a superset/subset of what OTel agent spans need? Should we publish a projection mapping? |
| **"Reliable Execution of Consequential AI Agent Actions Under Retries and Ambiguous Outcomes"** — Oswald, *Once Research*, technical note v1.0, 2026-09-26, DOI [10.5281/zenodo.22969881](https://doi.org/10.5281/zenodo.22969881) | Frames the ambiguous-outcome execution problem; logical operation identity, effect binding, durable outcome state, conservative `UNKNOWN` handling, reconciliation, selective protection of consequential mutations; reference implementation "Once"; explicitly does *not* claim universal exactly-once | Substantial overlap with our `outcome_unknown` + idempotency + capability tiers. Our deliverable differs in form: JSON Schema + 14 fixtures + cross-language conformance — something two vendors can *check against each other* rather than integrate a component | Is our contract a defensible *interop layer* for this model, or should it be folded into that work's terminology (e.g. rename `outcome_unknown` → `UNKNOWN`)? Being told "you are a profile of prior art" is a useful review. |
| **Agentic AI Foundation / ecosystem consolidation** (A2A joined AAIF, 2026-08) | Distribution and governance for agent interop | We do not want a competing standard; we want a reviewable artifact and, if it survives review, a home in an existing effort | Which venue should this be proposed to (AAIF working group? A2A extension? MCP extension?) rather than a standalone repo? |

**What we explicitly do NOT claim**: that no one has solved this; that our schema is frozen; that our implementation is a reference for multi-tenant or exactly-once delivery.

## 3. Cross-language conformance (the checkable part)

Python validator + independent Java implementation validate the **same fixtures**. Since v0.4.0 the agreement is **committed as evidence**, not asserted:

```bash
python -m pip install "jsonschema>=4.20"
python tools/contract_check.py --all     # valid=14/14, invalid=12/12
python tools/check_golden.py             # Python impl vs Java-generated golden: 33/33 byte-identical
```

- `tools/golden/` is produced by the **independent Java implementation**: 7 command outputs, 26 per-fixture digests, exit codes for 33 cases, and `MANIFEST.json` with sha256 per file;
- `tools/check_golden.py` reruns every case with the Python implementation and compares **bytes** — divergence fails CI;
- valid/invalid fixtures: same reject **paths** and exit codes across implementations;
- canonical JSON + sha256 (JCS): byte-identical across all 26 fixtures;
- exit-code discipline documented (`mvn` wrappers fold exit codes).

## 4. Review in 10 minutes (new)

1. **3 min** — read §1 table and `contract/status-transitions.json` (about 30 lines). Ask: *is any legal edge missing or unsafe?*
2. **2 min** — read the budget rules (`reserve → settle`, integer micro-USD). Ask: *can a retry double-charge under this wording?*
3. **2 min** — read the audit hash formula + canonical JSON rules. Ask: *could two implementations legitimately produce different hashes?*
4. **2 min** — skim `outcome_unknown` semantics + the ambiguous-outcomes row in §2. Ask: *is this the same object as their UNKNOWN, and are we duplicating it?*
5. **1 min** — answer any **one** of the questions in §2/§5 in an issue. A link is a valid review.

## 5. Questions we want answered (revised in v0.2)

1. **Profile or standalone?** Should this contract be an A2A profile / MCP extension rather than a separate schema? If yes, what is the minimal mapping (states, budget, audit)?
2. **State naming**: should `waiting_approval` become `input_required` (MCP vocabulary) and `outcome_unknown` become `UNKNOWN` (ambiguous-outcomes vocabulary)?
3. **Retry rule**: is "task-augmented calls are never retried" a rule that belongs in this contract, and at which layer?
4. **Budget semantics**: does `reserve → settle` with integer micro-USD survive provider-side pricing changes mid-run? What should happen when the price snapshot is missing (`cost = null` today)?
5. **Audit interop**: is our canonical JSON sufficient for cross-language chain verification, or should we adopt JCS (RFC 8785)?
6. **Duplication check**: given the four works in §2, which parts of this contract are *already standardised*, and which are genuinely missing? We will delete or fold anything already covered.

## 6. Known gaps (honest list)

1. "Mainstream frameworks do not expose agent-step reliability semantics" is based on README/documentation review, not source reading; undocumented precedents may exist.
2. OTel GenAI conventions are still in Development; our projection mapping is not written.
3. Spring Boot / JDK 25 support matrices were not verified (implementation avoids Lombok, targets Java 21).
4. Fixture-level transition tables are not yet forced to match the authoritative table (examples only).
5. Cost accounting records tokens only; without a price source `cost_micro_usd` is `null` (we refuse to fabricate numbers).
6. No multi-tenant or multi-run scheduling semantics in scope.

## 7. How to respond

- Open an issue with `[run-semantics]` (template provided) or comment on #1.
- "You are duplicating X; here is the spec link" is a complete and welcome review.
- We commit to listing **every** external review by name with accept/reject decisions, without rewriting or hiding discussion.

## Appendix A — Reference implementations (evaluable evidence)

| Implementation | Language | Coverage | Tests |
|---|---|---|---|
| Control plane (private) | Java 21 / JDK 25 | run creation, persisted idempotency store, budget ledger, injectable clock, HTTP API, hash-chained audit + verify, MCP policy proxy (tier filtering, per-call budget/ledger/audit), degraded executor, fault injection + startup reconciliation | 198 |
| Translation layer (private) | Python 3.14 | dispatch⇄run mapping, real HTTP client, headless worker event chain, approval ask/card, governance (wall clock, dead-man switch, usage accounting), delivery gate, Feishu gateway, daily report | 604 (+3 slow, skipped by default) |

Key behaviours have negative controls: a tampered audit chain is detected; `outcome_unknown` is never auto-retried; degraded mode denies writes but allows reads; approval denial leaves zero side effects.
