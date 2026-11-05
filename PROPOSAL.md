# Agent Run Semantics Contract v0.1 — Proposal

> **Status**: awaiting **at least 2 external technical reviews**. This is a *proposal for review*, not a finished standard.
> Response channel: **[open an issue](https://github.com/qlheric/agent-run-semantics-contract/issues/new?template=run-semantics-response.md)** with tag `[run-semantics]`.
> Every external review will be listed here by name with what we accepted / rejected and why.

## 1. The problem

Multi-agent systems have no shared contract for **"reliability semantics of one run"** (state machine, idempotency, budget, audit, timeout, retry):

- every framework defines its own run/step states and termination reasons ⇒ no interoperability;
- whether a side effect gets executed twice on retry/crash-recovery depends on implementation details;
- budget and audit units differ (float vs integer micro-USD, hash chain vs plain log);
- there is no checkable boundary between a **control plane** (scheduling / approval / delivery gate) and an **execution plane** (agent runtime).

This proposal is one minimal-but-complete answer: a JSON Schema + 14 valid/invalid fixtures + a cross-language conformance protocol, implemented independently twice (Java control plane, Python translation layer).

## 2. Contract shape (v0.1)

- A run document = `{spec, run, step[]}` plus root-level transition metadata; `additionalProperties: false` at every level.
- **18 required fields** (see `contract/README.md`). Most relevant here:

| Field | Semantics |
|---|---|
| `run.status` | 9 states: `created / running / waiting_approval / paused / degraded / succeeded / failed / cancelled / outcome_unknown` |
| status transitions | **single authority = `contract/status-transitions.json`** (schema only defines the shape; fixture-level tables are examples) |
| `run.budget.max_cost_usd` | **integer micro-USD**, floats rejected; two-phase `reserve → settle`, retries never reserve twice, difference refunded |
| `step.attempt` / `run.policy.max_attempts` | attempt number / retry ceiling |
| `step.idempotency_scope` | idempotency scope `run_id/step_id/tool` (nullable) |
| `run.termination.reason` | `completed / tool_error / budget_exhausted / deadline_exceeded / approval_denied / cancelled_by_caller / degraded_circuit_break` |
| `run.audit[]` | append-only hash chain: `hash = sha256(seq|ts|type|run_id|step_id|attempt|canonical(payload)|prev_hash)`, first `prev_hash` = 64 zeros |
| `run.tools[]` | tool catalog filtered by `policy.capability_tier` (`read/write/destructive`) |
| `outcome_unknown` | side effect may have happened: **automatic retry forbidden**; consult the idempotency ledger before recovery |

- **Canonical JSON**: keys sorted by code point, arrays in order, null-valued keys removed, NFC normalized, UTF-8 unescaped, floats always rejected (exit 2).

## 3. Cross-language conformance

The Python reference validator (`tools/contract_check.py`, dependency: `jsonschema`) and an independent Java implementation validate the **same fixture set**:

- all `valid/` pass, all `invalid/` rejected, **rejection paths and exit codes agree**;
- canonical JSON and `sha256` are **byte-identical** across 14 fixtures;
- exit-code discipline: Maven wrappers fold any non-zero into 1, so exit-code-sensitive comparison must call the JVM directly (documented in `contract/CONFORMANCE.md`).

Try it:

```bash
python tools/contract_check.py --all        # valid=9/9, invalid=5/5
python tools/contract_check.py --digest contract/fixtures/valid/step_full.json
python tools/contract_check.py --coverage   # enum/boundary coverage JSON
```

## 4. Reference implementations (evaluable evidence)

| Implementation | Language | Coverage |
|---|---|---|
| Control plane (private) | Java 21 / JDK 25 | run creation, persisted idempotency store, budget ledger, injectable clock, HTTP API, hash-chained audit + verify, MCP policy proxy (tier filtering, per-call budget/ledger/audit), degraded executor, fault injection + startup reconciliation — **182 tests** |
| Translation layer (private) | Python 3.14 | dispatch⇄run mapping, real HTTP client, headless worker event chain, approval ask/card, governance (global switch/wall clock/dead-man switch/usage 5 fields/rejection rate), eight-step delivery gate, two-level verify — **364 tests** |

Key behaviours have negative controls: a tampered audit chain must be detected by `verify()`; `outcome_unknown` is never auto-retried; degraded mode denies writes but allows reads; corrupted catalogs fail loud. Both implementations can be made available to reviewers on request.

## 5. Five questions we want answered

1. **Where should the authoritative transition-edge table live?** We chose a standalone file (`status-transitions.json`); schema-inline constants or code-derived edges are alternatives.
2. **Who should decide on retries for `outcome_unknown`?** Control plane (owns the ledger) or execution plane (owns tool context)? Counter-examples welcome.
3. **Is our canonical JSON enough for cross-language audit-chain interoperability?** Should we adopt JCS (RFC 8785) instead?
4. **Are the 18 required fields too many or too few?** Especially the relationship between `step.idempotency_scope` and tool-level idempotency-key templates, and the shape of step-level approvals.
5. **Does an equivalent standard already exist?** (MCP / A2A / OTel GenAI semantic conventions / CloudEvents / others). Please point out differences — we would rather become compatible than invent parallel work.

## 6. What this proposal does *not* claim

- Not a new agent framework, orchestration engine, or trace/eval platform.
- No claim of being first; if a standard already covers this, say so.
- The schema is not frozen: known gaps below are fair game.

## 7. Known gaps (honest list)

1. "Mainstream harnesses do not expose agent-step reliability semantics" is based on README grep, not source reading; undocumented precedents may exist.
2. "No governance layer on the JVM side" is based on three repositories' README capability tables.
3. Spring Boot support matrix for JDK 25, and Lombok/Byte Buddy compatibility, were not verified (implementation avoids Lombok, targets Java 21).
4. Maturity of OTel GenAI semantic conventions as of 2026-11 was not verified.
5. Fixture-level transition tables are not yet forced to match the authoritative table (examples only).
6. Cost accounting currently records tokens only; without a price source `cost_micro_usd` is `null` (we refuse to fabricate numbers).

## 8. How to respond

- Open an issue with tag `[run-semantics]` (template provided), or comment on an existing review issue.
- Pointing at an existing standard with links is a perfectly good review.
- We commit to listing **every** external review by name and recording accept/reject decisions, without rewriting or hiding the discussion.
