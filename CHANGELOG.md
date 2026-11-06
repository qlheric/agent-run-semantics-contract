# CHANGELOG — Agent Run Semantics Contract

All versions are **the same contract** (the `$id` never renames); the version set in `spec.version` lists compatible schema shapes.

## v0.4.0 — 2026-11-07

Semantic layer, driven by review 01 (§3.2/§3.4) and the "replay" recommendation.

- **Transition table**: added `created → waiting_approval`, `paused → failed`, `degraded → waiting_approval`, `degraded → outcome_unknown`, `outcome_unknown → cancelled`.
- **Safety**: removed `outcome_unknown → running` and added it to a `forbidden` list — validators fail if it is re-added. Leaving `outcome_unknown` for a terminal state now **requires `payload.evidence_ref`** (reconciliation evidence).
- **Audit**: new `state_transition` entry type with payload `{from, to, actor, cause, evidence_ref?}`; `actor ∈ {control_plane, executor, human, policy}`.
- **`--replay`** (included in `--all`): replays `state_transition` entries; checks the first `from == created`, chain continuity (`from == previous to`), edge legality against the authoritative table, and evidence for `outcome_unknown` exits. RFC 6901 paths on failure.
- Version identifiers aligned to `0.4.0`; `$id` carries the version segment.

**Fixtures**: 14 valid + 12 invalid. **Evidence**: Python `--all` green; Java `mvn test` 233/233; both implementations byte-identical on `--all/--schema/--coverage/--fields/--digest/--verify-chain/--replay`.

## v0.3.0 — 2026-11-06/07

Schema layer, driven by review 01 P0 findings.

- `step` is an **array** (multi-step runs representable); `run.reservations` added (`$defs/reservation`, `reserve → settle`, frozen price snapshot, retry reuses reservation id).
- `audit_entry.ts` → **`ts_ms: integer`** (epoch ms) — removes timezone/format divergence from the hash.
- Version set instead of `const` (`["0.2.0","0.3.0","0.4.0"]`); resolvable `$id` (`https://qlheric.github.io/agent-run-semantics-contract/v0.4.0/run_semantics.schema.json`).
- `x-status-transitions` no longer required; `side_effect_unknown` added; `error` nullable; `spec.framework` free-form string.
- `effective_tier = min(policy.capability_tier, tool.tier)`; four idempotency concepts collapsed to two (`idempotency_key`, `dedupe_scope`).
- Floats allowed in tool args (money remains integer micro-USD); `--fields --count` exports the real field count (51 at v0.3.0).
- **JCS (RFC 8785)** adopted for hashing, with UTF-16 code-unit key ordering (a non-BMP fixture proves both implementations agree).

## v0.2.0 — 2026-11-06

- Related-work section (A2A 1.0.0, MCP Tasks `2026-07-28`, OTel GenAI, ambiguous-outcomes note DOI 10.5281/zenodo.22969881); narrowed novelty claims; "review in 10 minutes" path.
- **Honest limitation recorded**: the earlier claim "two implementations, byte-identical" was not externally verifiable from this repo (no Java in the repo at that time).

## v0.1.0 — 2026-11-05

- Initial proposal: 9 states, transition table as a separate artifact, integer micro-USD budget, hash-chained audit, capability tiers, `outcome_unknown`.
- **Known defects at this version** (found by review 01, fixed in v0.3/v0.4): validator did not read the transition table; fixture audit hashes were placeholders; `reserve → settle` was prose-only; `step` was a single object; `outcome_unknown → running` was legal; canonical JSON conflicted with JCS.
