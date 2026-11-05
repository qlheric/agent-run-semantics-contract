# Agent Run Semantics Contract v0.1（提案 / proposal）

**A cross-language reliability contract for agent runs** — status machine, idempotency, budget (integer micro-USD), audit hash chain, and `outcome_unknown` semantics.

> **Status: awaiting at least 2 external technical reviews.** This is a proposal, not a finished standard.
> 👉 **[Read the proposal](PROPOSAL.md)** · **[Give a review](https://github.com/qlheric/agent-run-semantics-contract/issues/new?template=run-semantics-response.md)**

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
  contract_check.py           # Python reference validator (stdlib only)
```

## Try it

```bash
python -m pip install "jsonschema>=4.20"   # only dependency of the validator
python tools/contract_check.py --all
# rejected contract/fixtures/invalid/negative_budget.json path=/run/budget/max_cost_usd
# ...
# valid=9/9 passed
# invalid=5/5 rejected

python tools/contract_check.py --digest contract/fixtures/valid/step_full.json
# canonical: {...}
# sha256: <64 hex>

python tools/contract_check.py --coverage   # enum/boundary coverage as one-line JSON
python tools/contract_check.py --fields     # required field paths as one-line JSON
```

Exit codes: `0` all good · `2` invalid fixture / rejection path mismatch · `1` environment error.

## Why we think this is worth reviewing

- **A single authority for transitions** instead of per-framework state machines.
- **Integer micro-USD everywhere** with `reserve → settle` (retries never reserve twice, difference refunded).
- **Hash-chained audit** with a byte-exact canonical JSON so two languages can verify each other's chains.
- **`outcome_unknown` is a first-class state**: side effects may have happened; automatic retry is forbidden.
- Implemented independently twice (Java control plane, Python translation layer) and compared **byte for byte** across the same fixtures.

## Review questions (details in [PROPOSAL.md](PROPOSAL.md#5-five-questions-we-want-answered))

1. Where should the authoritative transition table live?
2. Who decides `outcome_unknown` retries — control plane or execution plane?
3. Is our canonical JSON enough, or should we adopt JCS (RFC 8785)?
4. Are the 18 required fields too many / too few?
5. Does an existing standard already cover this (MCP / A2A / OTel GenAI / CloudEvents…)?

## Honest limitations

See [PROPOSAL.md §7](PROPOSAL.md#7-known-gaps-honest-list): README-grep evidence, unverified support matrices, fixture tables not yet aligned with the authoritative one, cost accounting tokens-only.

## License

- Documentation (`*.md`): CC BY 4.0
- Code (`tools/`, workflows): MIT

No credentials, no personal data, no internal process documents are included in this repository.
