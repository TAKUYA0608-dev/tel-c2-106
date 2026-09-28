# Template Design Specification — TEL-C2-106

Telecom Alarm-Correlation Evidence Queue Agent (Cat 2, GraphNode-in-main).

## Position in AgentCore Architecture

- **Agent Class**: `TelecomAlarmCorrelationQueueAgent` (module-level alias of `Graph`)
- **L1 Base**: AgentBaseGraph (L1 direct — Cat 2 GraphNode-in-main; **not** AutonomousBaseGraph)
- **Category**: Cat 2 — orchestrates a fixed multi-step workflow to produce one job-to-be-done deliverable
  (an AlarmCorrelationEvidenceQueue for a telecom NOC).
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); complex fields are JSON strings (ADR-005)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only — no `config` param)
  - Graph: composition (`register_nodes()` for node substitution; domain complexity behind a `GraphNode`)

## Architecture Overview

### Node Configuration (outer 5-slot backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema/session/trust setup | user_input | caller_trust_level, session_id | InitializeNode (default) |
| pre_process | `EvidenceIngest` (input side) — S-1 normalisation (NFKC / strip control / length cap), S-2 SensitiveDataDetectAndMinimise (mask/tokenise tenant/site/element identifiers + minimum-necessary excerpts; **`event_id`/`element`/`ne_id` UNCONDITIONALLY tokenised to `evt:<sha8>`/`ne:<sha8>` — no syntactic passthrough**; **provenance `source` resolved to a citation ONLY if it names an authorized system of record → privacy-tokenised `src:<sha8>`, else dropped to `None`** so S-3 blocks; alarm free-text length-capped + credential/My-Number/email/phone redacted; `alarm_type` constrained to a safe enum token; `scope`/`period` hygiened), alarm-slot extraction | user_input | validated_input, input_format, enriched_context, (error_code) | PreProcessNode (FunctionNode) |
| main | `AlarmCorrelationWorkflowGraphNode` — wraps inner `AlarmCorrelationWorkflow` (composition criterion #9) | validated_input | result, alarm_count, group_count, noc_review_required, (error_code), status | GraphNode (subgraph) |
| post_process | `EvidenceQueueCompose` — S-3 output gate: **fail-closed per-group citation completeness** (a grounded queue with any group missing a verifiable source citation or correlation-rule id/version → `needs_review` degrade, queue body withheld, `error_code=CITATION_INCOMPLETE`) + injection-marker neutralisation + credential/My-Number/email/phone/element-name redaction + DRAFT advisory disclaimer, S-4 audit | result | formatted_output, disclaimer, audit_logged, (error_code) | PostProcessNode (FunctionNode) |
| finalize | build response envelope | formatted_output | output, status | FinalizeNode (default) |

### Inner workflow (`src/graph/domain_workflow_graph.py` — BaseGraph, linear + per-node skip guard)

```
START → alarm_ingest → correlation_group → evidence_interpret → noc_review_gate → END
```

| Inner Node | Responsibility | Skip guard |
|------|---------------|-----------|
| alarm_ingest | Deterministic ingest + normalise of the supplied approved alarm export / topology / maintenance windows / correlation policy; validate required fields (event_id / ts / element / alarm_type), parse timestamps to epoch, apply the bounded window; set `alarm_count`. **0 valid alarms → `error_code=NO_ALARMS` → out-of-scope safe answer** | — (first node; emits `.skip` on rejected/no-alarm input) |
| correlation_group | **Deterministic (Tool-split, `shared/tools/correlation` reference)**: bundle alarms into candidate groups by policy rule — topology-adjacency join + time-window proximity threshold + alarm-type co-occurrence. Single-alarm clusters are not groups. Set `group_count` | no-op `return {}` (after `.skip` emit) on `error_code` / `alarm_count == 0` |
| evidence_interpret | **Bounded interpretation (deterministic; no LLM)** + **pre-interpretation containment**: map each member's alarm free-text to the policy rule vocabulary (allow-list keyword/shingle match), select and cite supporting event IDs, attach an uncertainty label (high / medium / needs-review) on partial match / maintenance-overlap ambiguity / missing topology / contained untrusted content. Compose the `AlarmCorrelationEvidenceQueue` deliverable with per-group citations. On 0-group (alarms present) → grounded-but-uncited `no_correlations`; on 0-alarm/rejected → out-of-scope safe answer | emits safe answer on `error_code` / no alarms |
| noc_review_gate | Deterministic **NOC review gate (HumanApprovalGate)**: every candidate group requires NOC human review — set `noc_review_required=True` + `review_status="pending_noc_review"`, record the material groups (needs-review / high-priority) into the queue. The agent **never** accepts/rejects a group or assigns an owner | no-op `return {}` (after `.skip` emit) on `error_code` / `group_count == 0` (safe / no-correlation answer needs no review gate) |

> **Conditional edges do not propagate across the subgraph boundary** (GraphNode wraps the inner graph),
> so the inner topology is a **static linear backbone with per-node skip guards** — the portable Cat 2 form
> shipped across the fleet. `add_conditional_edges` is intentionally **not** used inside the subgraph.

### Data Flow

```
START → initialize → pre_process → main(GraphNode) → {route} → post_process → finalize → END
                                        ↓ (retry, max 3)
                                     pre_process
```

**Degraded / rejected path (mandatory contract).** An injection marker, an oversize payload, empty input,
or zero valid alarms never sets `status=ERROR`. Instead the node returns `status=SUCCESS` **plus an
`error_code`** (`INJECTION_REJECTED` / `INPUT_TOO_LONG` / `INPUT_REJECTED` / `NO_ALARMS`). This is
deliberate: in the production framework `status=ERROR` short-circuits `AgentBaseGraph.route()` straight to
`finalize`, so **`main`/`post_process` would be skipped and the mandatory DRAFT disclaimer + S-3 redaction
+ S-4 terminal audit would never run**. With `SUCCESS + error_code`, `route()` reaches `post_process`,
which always emits the out-of-scope safe answer, disclaimer, and audit. On the injection/oversize path
`pre_process` **discards the offending body** (`validated_input="{}"`, `user_input` cleared) so no rejected
content is ever processed. Because `GraphNode.extract_input()` passes only `validated_input` into the fresh
inner state, `merge_output` surfaces the **outer** `error_code` first (`state.get("error_code") or
sub_result.get("error_code")`) so a pre-stage rejection code survives to the terminal S-4 audit.

### Injection / untrusted-content defence — three separate layers (proof-tested independently)

Approved alarm exports can legitimately contain injection-like text in alarm free-text, so defence is
layered rather than a single input reject:

1. **S-2 caller-boundary reject (`pre_process`)** — a prompt-injection marker or oversize on the raw
   `user_input` (the caller prompt) is hard-rejected to a degraded `SUCCESS + error_code=INJECTION_REJECTED`
   / `INPUT_TOO_LONG`, body discarded (this is the mandatory execute-level degraded reject).
2. **Pre-interpretation containment (`evidence_interpret`, deterministic)** — alarm free-text is treated
   strictly as **data**: embedded prompt-like content is never followed; interpretation is schema-constrained
   to the policy rule vocabulary + retrieved rule/event IDs (allow-list); a member carrying prompt-like /
   non-allow-listed content routes its group to **needs-review** (contained, not interpreted). This is a
   distinct layer from S-2 (which masks identifiers) and S-3 (which sanitises output).
3. **S-3 output gate (`post_process`)** — injection-marker neutralisation on the composed queue +
   fail-closed per-group citation completeness.

### State Definition (`src/schemas/state.py`)

**Platform masking (S-2).** The platform's personal-data pass runs before every node and replaces e-mail
addresses, phone numbers and runs of two or more Title-Case words with `[MASKED]`; it cannot be switched off.
Element ids are tokenised by hashing, so two different elements that both arrive as `[MASKED]` share one
`ne:` surrogate. Rule: `pre_process` records the surrogates of element-id fields whose value contains
`[MASKED]` (`validated_input.masked_elements`); `evidence_interpret` labels every group with such a member
`needs_review` (reason in `advisory_rationale`, group field `limitation: "ELEMENT_ID_MASKED"`), and the queue
carries `limitations: ["ELEMENT_ID_MASKED"]` (codes only) with the explanation in `message`. Masking only
merges ids, so `no_correlations` and groups made only of unmasked ids are unaffected; their output is the
previous one plus `limitations: []`.

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| validated_input | NotRequired[str] | JSON `{alarms[], topology[], maintenance_windows[], correlation_policy{}, scope, period, masked_elements[]}` from pre_process (credential/My-Number redacted, identifiers tokenised; `masked_elements` = surrogates of element ids that arrived containing `[MASKED]`) | no |
| input_format | NotRequired[str] | `json` / `text` / `empty` / `rejected` | no |
| enriched_context | NotRequired[str] | JSON `{source, channel}` (read-only caller context) | no |
| normalized_alarms | NotRequired[str] | JSON per-alarm canonical signals (tokenised event_id / element, epoch ts, alarm_type, vocab terms, source) | no |
| alarm_count | NotRequired[int] | alarms ingested (0 → out-of-scope safe answer) | no |
| candidate_groups | NotRequired[str] | JSON candidate correlation groups (rule id/version, member event IDs, time range) | no |
| group_count | NotRequired[int] | correlation groups formed (0 with alarms present → grounded-but-uncited `no_correlations`) | no |
| result | NotRequired[str] | JSON assembled AlarmCorrelationEvidenceQueue (incl. noc_review) | no |
| noc_review_required | NotRequired[bool] | True once the NOC review gate flags candidate groups | no |
| review_status | NotRequired[str] | `pending_noc_review` / `not_required` | no |
| formatted_output | NotRequired[str] | JSON final response envelope (queue + disclaimer) | no |
| disclaimer | NotRequired[str] | mandatory DRAFT / advisory-only disclaimer | no |
| audit_logged | NotRequired[bool] | True once terminal audit event emitted | no |
| error_code | NotRequired[str] | `INPUT_REJECTED` / `INJECTION_REJECTED` / `INPUT_TOO_LONG` / `NO_ALARMS` / `CITATION_INCOMPLETE` | no |
| error_message | NotRequired[str] | operator-facing detail | no |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types); complex fields serialized as JSON strings (ADR-005)
- No JWT, API keys, credentials in State (checkpoint DB leakage) — pre_process input hygiene redacts them
- **Opaque-id boundary — privacy tokenize vs provenance validation are SEPARATE (primary defense = input side).**
  - **Privacy (identifiers).** `event_id` / `element` / `ne_id` are **unconditionally** tokenized to
    `evt:<sha8>` / `ne:<sha8>` — there is NO syntactic "this looks like a safe id" passthrough (a bare name
    such as `Alice` / `NE.Core.1` with no spaces/symbols must NOT pass through a loose grammar). Tokenizing
    hides PII / topology detail; it makes **no** claim that the value is authorized. Elements are tokenized
    deterministically so the topology-adjacency join still resolves on the surrogate.
  - **Provenance (citations).** A caller `source` becomes a grounded citation **only when it resolves to an
    authorized system of record** (`AUTHORIZED_PROVENANCE_SYSTEMS` — a *semantic* allowlist of authorized
    EMS/OSS/fault-management systems, not a character class). Then it is privacy-tokenized to `src:<sha8>`
    (raw label never verbatim). Any other value — an element name, `unknown`, a fabricated string, **or a
    value merely SHAPED like a surrogate (`src:1a2b3c4d`)** — is **not** verifiable provenance → `None` →
    S-3 blocks the queue as `CITATION_INCOMPLETE` (fail-closed). "Tokenized" is never sufficient for a
    citation. Provenance is resolved **exactly once** (pre_process / S-1); downstream never re-resolves an
    internal `src:<sha8>`, so a forged surrogate can never imitate one.
  - Only values already in the strict surrogate namespace (`evt:` / `ne:` / `src:` + 8 hex — which a name
    can never match) pass through, making re-tokenization idempotent. `alarm_type` is a safe enum token or
    `unknown`; `scope`/`period` are hygiened. Every output field is derived (rule id/version / tokenised IDs
    / allow-listed vocab-match rationale / counts) — raw alarm free-text and arbitrary caller fields are
    never copied into the output. The whole-report redactor is defense-in-depth only.
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, caller_trust_level) — read-only inside nodes
- [x] **S-2**: `_extra_security_gate_input(self, state) -> dict` on `PreProcessNode` — size cap +
      prompt-injection markers + field-level input hygiene. SDK 1.0.0 contract: **MUST NOT raise, and MUST
      NOT return `status=ERROR`**. A rejection is surfaced as a **degraded `SUCCESS + error_code`**
      (`INJECTION_REJECTED` / `INPUT_TOO_LONG`); `pre_process.execute()` re-checks the same conditions
      (the `@final` hook is not invoked by the local stub framework) and discards the offending body so the
      pipeline reaches `post_process` and always emits the disclaimer + audit.
- [x] **S-3**: `PostProcessNode.execute()` enforces **fail-closed per-group citation completeness** — a
      grounded queue with any group missing a verifiable `source` citation or a correlation-rule id/version
      is not presented; it degrades to a safe `needs_review` answer (queue body withheld,
      `error_code=CITATION_INCOMPLETE`, still SUCCESS so the disclaimer + S-4 audit run). A whole-report
      redactor re-redacts credential / My-Number / email / **phone** / **element/company-name** leakage and
      neutralises injection markers. `_extra_security_gate_output(self, result) -> dict` additionally
      verifies the mandatory DRAFT disclaimer is present and MAY raise to block.
- [x] **S-4**: `emit_trace_event()` inside **every** `execute()` path (including skip / safe branches) —
      domain event only (never `node_start`/`node_complete` which `BaseNode.__call__()` emits automatically).
      Counts / uncertainty distribution / error_code only — never a raw element name, alarm text, or source.

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass (`PreProcessNode`, `PostProcessNode`, and the four inner nodes) → framework
>   `@final` gate always runs automatically; extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only.
> - `GraphNode` (`AlarmCorrelationWorkflowGraphNode`) → deliberate no-op (the wrapped subgraph nodes' gates already apply).

### Trust Level (S-1)
All concrete `FunctionNode` subclasses declare `required_trust_level = TrustLevel.VERIFIED_EXTERNAL`
explicitly (gate-trust-level-check), matching the agent-level `required_trust_level` in `config/agent.yaml`.
`GraphNode` is excluded by design (delegated S-1).

### Determinism (no LLM)
The template is **fully deterministic** — no LLM is used. Correlation grouping (topology-adjacency join +
time-window proximity + alarm-type co-occurrence), free-text-to-policy-vocabulary mapping (allow-list
keyword/shingle match), and uncertainty labelling are pure arithmetic + threshold banding + keyed KB
composition (the correlation policy rule vocabulary in `src/services/service.py`), so the evidence queue is
reproducible and auditable. `config/agent.yaml` declares no model and `pyproject.toml` declares no LLM
dependency. The pure correlation computation is carved into a `shared/tools/correlation` reference function
(§2-3 of the proposal); the Agent value concentrates in free-text interpretation + uncertainty + advisory
synthesis.

### Composition Pattern
- **Pattern**: GraphNode (subgraph) — outer `AgentBaseGraph` 5-slot backbone with a `GraphNode` in the `main`
  slot wrapping an inner `BaseGraph` (`AlarmCorrelationWorkflow`).
- **Composition target**: `src/graph/domain_workflow_graph.py::AlarmCorrelationWorkflow`
- **Subgraph caching**: `get_subgraph()` caches the inner graph on the **class attribute** (`AlarmCorrelationWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance state per §9; built once).
- **Error propagation strategy**: `propagate` — an inner ERROR surfaces as `SubgraphError`; the deterministic
  degraded path (0-alarm / rejected) instead returns `status = SUCCESS + error_code` and a safe answer.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0) — PB-4
- [x] Import targets: `framework/` and `shared/` only (via `src.utils.audit` fallback shim)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | **AgentBaseGraph** | AutonomousBaseGraph | **AgentBaseGraph** | Fixed multi-step workflow, no autonomous reasoning loop — Cat 2 |
| Composition pattern | FunctionNode-in-main (Cat 1) | **GraphNode-in-main (Cat 2)** | **GraphNode-in-main** | Domain workflow has ≥4 ordered steps → encapsulate behind a subgraph |
| Inner topology | conditional edges | **linear + skip guards** | **linear + skip guards** | Conditional edges do not propagate across the subgraph boundary |
| Correlation / interpretation | LLM narrative | **deterministic correlation + allow-list vocab match** | **deterministic** | Auditable, reproducible; no LLM — see Determinism |
| Injection defence | single input reject | **3 layers: S-2 reject + pre-interpretation containment + S-3** | **3 layers** | Approved exports may contain injection-like alarm text |
| Human sign-off | agent auto-decides group | **NOC review gate (candidate only)** | **NOC review gate** | Advisory only; the actionable-group decision is a NOC human's, never the agent's |
| Degraded path | status=ERROR | **status=SUCCESS + error_code** | **SUCCESS + error_code** | ERROR would skip post_process (S-3/S-4) in production FW |

## Open Items (deferred to Stage ③ Implementation MR)
- Node `execute()` bodies (`alarm_ingest`, `correlation_group`, `evidence_interpret`, `noc_review_gate`,
  rewritten `pre_process` / `post_process`), the inner `AlarmCorrelationWorkflow`, `AlarmCorrelationService`
  (correlation policy rule vocabulary + deterministic grouping + interpretation + uncertainty),
  `src/utils/audit.py` (S-4 shim), unit / integration / boundary tests, and docs/03 + docs/07 land in the
  Stage ③ implementation MR. This design MR ships `docs/02_design.md` + `src/schemas/state.py` only.
