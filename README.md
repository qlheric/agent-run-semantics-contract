# Agent Run Semantics Contract v0.2（提案 / proposal）

**A cross-language reliability contract for agent runs** — states & legal transitions, idempotency scope, integer micro-USD budget (`reserve → settle`), hash-chained audit with canonical JSON, capability-tiered tools, and a first-class `outcome_unknown` state.

> **Status: v0.2-draft, awaiting at least 2 external technical reviews.**
> 👉 **[Read the proposal](PROPOSAL.md)** · **[Give a 10-minute review](https://github.com/qlheric/agent-run-semantics-contract/issues/new?template=run-semantics-response.md)** · **[Discussion issue #1](https://github.com/qlheric/agent-run-semantics-contract/issues/1)**
>
> **New in v0.2**: a *Related work & differences* section comparing A2A 1.0.0, the MCP Tasks extension (`2026-07-28`), OpenTelemetry GenAI conventions, and the "ambiguous outcomes" technical note (DOI 10.5281/zenodo.22969881) — including the parts where **they already cover what we thought was missing**, and a *review-in-10-minutes* path.

## Why this exists

Protocols standardise *how agents talk* (A2A) and *how long-running tool calls are tracked* (MCP Tasks). They deliberately leave to implementations the decisions that determine whether a retry is safe:

- which state transitions are legal (and which are terminal),
- whether a side effect may already have happened when the outcome is unknown,
- how budget is reserved and settled (integer micro-USD, never double-charged),
- what an auditable, cross-language-verifiable record looks like.

This repo proposes those as an **interoperable contract**: JSON Schema + fixtures + a conformance protocol. Not a framework, not a runtime.

## What's in this repo

```
contract/
  run_semantics.schema.json   # the 18 required fields, enums, shapes
  status-transitions.json     # single authoritative state-transition table (9 states)
  README.md                   # field semantics & discipline
  CONFORMANCE.md              # cross-language conformance protocol
  fixtures/valid/*.json       # 9 valid examples
  fixtures/invalid/*.json     # 5 negative examples (must be rejected, with paths)
tools/
  contract_check.py           # Python reference validator
```

## Try it (2 minutes)

```bash
python -m pip install "jsonschema>=4.20"   # only dependency of the validator
python tools/contract_check.py --all
# valid=9/9 passed
# invalid=5/5 rejected
python tools/contract_check.py --digest contract/fixtures/valid/step_full.json
```

## The four questions we most want answered

1. **Profile or standalone?** Should this be an A2A profile / MCP extension instead of its own schema?
2. **Vocabulary**: should `waiting_approval` be `input_required` and `outcome_unknown` be `UNKNOWN` (aligning with MCP Tasks and the ambiguous-outcomes model)?
3. **Duplicate check**: given §2 of the proposal, which parts are *already standardised*? We will delete or fold anything covered.
4. **Audit interop**: is our canonical JSON enough for cross-language chain verification, or should we adopt JCS (RFC 8785)?

## Honest limitations

See [PROPOSAL.md §6](PROPOSAL.md#6-known-gaps-honest-list). Summary: related-work survey is documentation-level, OTel mapping not written, no multi-tenant scope, cost accounting is tokens-only (`cost = null`, we do not fabricate prices).

## License

- Documentation (`*.md`): CC BY 4.0
- Code (`tools/`, workflows): MIT

No credentials, no personal data, no internal process documents are included in this repository.
