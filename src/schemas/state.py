"""TEL-C2-106 — Agent state (Telecom Alarm-Correlation Evidence Queue, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on read.

Read-only / advisory: the agent ingests an approved, de-identified alarm export (+ topology / maintenance
windows / a carrier-owned correlation policy) and produces an **AlarmCorrelationEvidenceQueue** deliverable
for NOC review — it never queries live telemetry, diagnoses root cause, recommends remediation, suppresses
alarms, changes tickets, or operates equipment. The final correlation decision (which candidate group is
actionable) is always an authorized NOC human's, gated by a mandatory NOC review gate (HumanApprovalGate).

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the alarm-correlation evidence-queue workflow."""

    # ── pre_process (EvidenceIngest input side, S-1/S-2 validated + hygiened request) ──
    validated_input: str  # JSON: {alarms[], topology[], maintenance_windows[], correlation_policy{}, scope, period}
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (alarm_ingest → correlation_group → evidence_interpret → noc_review_gate) ──
    normalized_alarms: (
        str  # JSON: [{event_id(evt:<sha8>), ts_epoch, element(ne:<sha8>), alarm_type, vocab_terms[], source}]
    )
    alarm_count: int  # alarms ingested (0 → out-of-scope safe answer)
    candidate_groups: (
        str  # JSON: [{group_id, correlation_rule_id, correlation_rule_version, member_event_ids[], time_range}]
    )
    group_count: int  # correlation groups formed (0 with alarms present → "no_correlations" grounded-but-uncited)
    result: str  # JSON: assembled AlarmCorrelationEvidenceQueue (incl. noc_review)
    noc_review_required: bool  # True once the NOC review gate flags candidate groups for review
    review_status: str  # "pending_noc_review" | "not_required"

    # ── post_process (EvidenceQueueCompose — S-3 gate + S-4 audit) ────────────
    formatted_output: str  # JSON: final response envelope (queue + disclaimer)
    disclaimer: str  # mandatory DRAFT / advisory-only disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ───
    # INPUT_REJECTED | INJECTION_REJECTED | INPUT_TOO_LONG | NO_ALARMS | CITATION_INCOMPLETE
    error_code: str
    error_message: str  # operator-facing detail
