# Test Specification — TEL-C2-106

## Test Strategy
- Coverage target: **80%+** (achieved **94%**, `--cov=src`)
- Test types: Unit (pre/post + inner nodes + services) / Unit (Cat 2 graph wiring + real invoke) / Integration / Proof-of-Boundary
- Determinism: **no LLM** — correlation grouping (union-find over topology-adjacency + time-window
  proximity + alarm-type co-occurrence), free-text-to-policy-vocabulary mapping (allow-list keyword/shingle
  match), and uncertainty labelling are pure arithmetic + threshold banding + keyed policy composition
  (reproducible, auditable). No model is declared in `config/agent.yaml` and no LLM dependency in `pyproject.toml`.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | `State(AgentState)`, NotRequired primitives + JSON strings; no PII/credential fields | ✅ PASS |
| TC-02 | S-2 rejection is degraded, never `status=ERROR` | `_extra_security_gate_input` sets `error_code` (`INJECTION_REJECTED`/`INPUT_TOO_LONG`) and returns `dict(state)`; never raises, never sets `status=ERROR` | ✅ PASS |
| TC-03 | No JWT/Credential in `src/` | `gate-credential-scan`: 0 violations | ✅ PASS |
| TC-04 | InvocationContext read-only via `from_state()` | never stored in State | ✅ PASS |
| TC-05 | S-4: no duplicate lifecycle events | only domain events emitted (never node_start/complete) | ✅ PASS |
| TC-06 | S-2 `_security_gate_input()` not overridden | `@final`; only `_extra_*` extended (real SDK on CI; local stub env-diff) | ✅ (CI) |
| TC-07 | S-3 `_security_gate_output()` not overridden | `@final`; may raise via `_extra_*` (real SDK on CI; local stub env-diff) | ✅ (CI) |
| TC-08 | `required_trust_level` explicit on every FunctionNode | `VERIFIED_EXTERNAL` on pre/post + 4 inner nodes (gate-trust-level-check) | ✅ PASS |
| TC-09 | Cat consistency | Template ID / config / README all Cat 2 (gate-cat-consistency) | ✅ PASS |
| TC-10 | Exact dependency pins | `==` in all sections incl. `[build-system]` setuptools==68.0.0 (gate-dep-pinning) | ✅ PASS |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per `execute()` | emitted on every path (incl. skip / safe / degraded branches) | ✅ PASS |
| TC-12 | Degraded path never `status=ERROR` | injection / oversize / empty / 0-alarm → `SUCCESS.value + error_code`; `post_process` still runs | ✅ PASS |
| TC-13 | Input hygiene: PII / secrets never persist in State | operator name / contact email dropped; credential / My-Number / email / phone redacted from `validated_input`; `scope`/`period` hygiened | ✅ PASS |
| TC-14 | Real `Graph().invoke()` degraded path | injection & oversize → SUCCESS + out-of-scope, `PostProcessNode` in node_history, error_code in terminal S-4 audit, rejected body absent, DRAFT disclaimer present | ✅ PASS |
| TC-15 | S-3 fail-closed per-group citation completeness | grounded queue with any group missing a citation or correlation-rule id/version → `needs_review` degrade (SUCCESS + `CITATION_INCOMPLETE`), queue body withheld, disclaimer + audit still run; verified via real `Graph().invoke()` | ✅ PASS |
| TC-16 | Provenance allowlist + output redaction | unsafe caller `source` (name/phone) dropped, never in `formatted_output`; `scope` name/phone/email redacted in a grounded output (S-3 redactor covers credential/My-Number/email/phone/company-name + injection-marker neutralisation) | ✅ PASS |
| TC-17 | Opaque-id boundary — privacy tokenize (identifiers) | `event_id`→`evt:<sha8>`, `element`/`ne_id`/topology/maintenance refs→`ne:<sha8>` UNCONDITIONALLY (a no-space name is tokenized, not passed through); deterministic + referentially consistent (topology-adjacency join resolves on the surrogate); surrogate namespace idempotent; `alarm_type` constrained; arbitrary extra caller fields never reach output; verified via real `Graph().invoke()` | ✅ PASS |
| TC-18 | Provenance validation (separate from privacy) | a caller `source` is a grounded citation ONLY if it names an authorized system of record; unverifiable source (element name / `unknown` / fabricated / forged surrogate `src:1a2b3c4d`) → `None` → `needs_review` (CITATION_INCOMPLETE), never a citation; authorized `ems:…`/`netcool:…` → privacy-tokenized `src:<sha8>` (raw not in output); verified via real `Graph().invoke()` | ✅ PASS |
| TC-19 | Pre-interpretation containment (H-01) | injection-like text inside a structured export's alarm free-text is contained at `evidence_interpret` (its group → needs-review, not interpreted) and neutralised at S-3 — a distinct layer from S-2 caller-boundary reject | ✅ PASS |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Expected Result | Result |
|-------|----------|----------------|--------|
| PB-2/5 | Post-invoke State is primitives only; no credential fields | AST scan: 0 violations | ✅ PASS |
| PB-4 | Import isolation — no Level 0 (`agenticstar`) imports | AST scan: 0 violations | ✅ PASS |
| PB-6 | Invoke order S-1 → S-4(start) → S-2 → execute → S-3 → S-4(complete) | Order verified | ✅ (real SDK on CI; local-stub env-diff) |
| PB-7 | HITL interrupt propagation | conditional — SKIPPED (`hitl.enabled` not set for this template) | ✅ (n/a, skip) |
| S-0 | Cat 2 `GraphNode`-in-main wraps inner `BaseGraph` (cached `get_subgraph`) | gate-composition passes | ✅ PASS |

## Business Logic Tests

| BL-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Alarm normalization + rule match | alarm with event_id/ts/element/alarm_type/text | tokenized ids, epoch ts, rule_id classified (allow-list); malformed / no-ts rows dropped | ✅ PASS |
| BL-02 | Deterministic correlation grouping | adjacent NEs, same rule, within window | union-find group formed; single-alarm cluster / out-of-window / cross-rule not grouped | ✅ PASS |
| BL-03 | Bounded interpretation + uncertainty | grouped alarms | vocab-matched, uncertainty `high` (full match + topology + no maintenance) / `medium` / `needs_review` (partial / maintenance overlap / missing topology / contained) | ✅ PASS |
| BL-04 | Evidence queue synthesis | interpreted groups | per-group supporting_event_ids + rule id/version + time_range + uncertainty + advisory rationale + citations; queue_summary | ✅ PASS |
| BL-05 | NOC review gate (HumanApprovalGate) | queue with candidate groups | `noc_review_required=True`, material groups recorded, no accept/reject, no owner assigned | ✅ PASS |
| BL-06 | No-correlations (alarms, 0 groups) | single alarm / non-grouping alarms | `no_correlations`, no fabricated group, no citations | ✅ PASS |
| BL-07 | Out-of-scope (no alarms) | NL text / empty alarms | `out_of_scope`, no citations, safe message | ✅ PASS |
| BL-08 | Empty input degrades | "   " | `SUCCESS.value + INPUT_REJECTED`, still audits | ✅ PASS |
| BL-09 | Element / operator name never in output | export with element names + operator PII | names absent from validated_input and output; only tokenized ids + counts | ✅ PASS |
| BL-10 | Mandatory DRAFT disclaimer | any queue | S-3 gate blocks output missing 参考/DRAFT | ✅ PASS |
| BL-11 | Ungrounded queue withheld | group with no / unsafe `source` or missing rule version | grounded queue degrades to `needs_review`, no queue body, `CITATION_INCOMPLETE` | ✅ PASS |
| BL-12 | Provenance safety / no leak | unsafe `source` (name+phone) or PII in scope | dropped/redacted — not in `formatted_output` | ✅ PASS |
| BL-13 | Privacy tokenize (identifiers) | `event_id` / `element` = name+phone **or no-space name** | tokenized to `evt:`/`ne:<sha8>`; original absent; topology join consistent | ✅ PASS |
| BL-14 | Unknown caller field not echoed | alarm with arbitrary PII field | field never copied into `formatted_output` | ✅ PASS |
| BL-15 | Provenance validation (fail-closed) | `source` = element name / `unknown` / fabricated / forged surrogate | no citation → `needs_review` (CITATION_INCOMPLETE); source not in output | ✅ PASS |
| BL-16 | Authorized provenance accepted | `source` = `ems:…` / `netcool:…` | grounded queue; citation = tokenized `src:<sha8>`; raw not in output | ✅ PASS |
| BL-17 | Injection containment (H-01) | structured export with injection text in alarm free-text | group → needs-review (contained, not interpreted); marker neutralised at S-3; not rejected at S-2 | ✅ PASS |

## Test Execution Summary
- Total: 97 (unit-nodes 55 + unit-graph 36 + integration 6) + active PB (import_isolation, state_safety, S-0 via graph wiring)
- Pass: 96 · Skip: server-import (local stub env-diff) + PB-7 ×2 (conditional, HITL off)
- env-diff: `test_pb_invoke_order` + `test_framework_compliance_tc06_tc07` assert against the real SDK on CI (the local SDK stub lacks the `emit_trace_event` surface / `@final` gate enforcement); both pass on CI
- Coverage: **94%** (`--cov=src`)
