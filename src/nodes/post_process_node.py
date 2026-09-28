"""TEL-C2-106 — post_process node: EvidenceQueueCompose (S-3 output gate + S-4 audit).

S-3 (fail-closed): **enforce** per-group citation completeness — a grounded evidence queue with any group
missing a verifiable source citation or a correlation-rule id/version is never presented; it degrades to a
safe ``needs_review`` answer with the queue body withheld (``error_code=CITATION_INCOMPLETE``, still SUCCESS
so post/S-4/disclaimer run). Neutralise injection markers on the composed queue, re-redact any credential /
My-Number / email / phone / element-or-company-name leakage (defense-in-depth), and append the mandatory
DRAFT advisory disclaimer — the queue is a decision aid, not a correlation decision; the final correlation
decision (which candidate group is actionable) and any operational action are an authorized NOC human's,
gated by the NOC review gate. S-4: emit an audit event (counts / uncertainty distribution / review flag /
error_code only — never a raw element name, alarm free-text, or source). Runs on the full queue, the
citation-blocked branch, and the out-of-scope / no-correlation safe branches.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event

_DISCLAIMER = (
    "本レポートは提供された認可済み・非識別化アラームエクスポートと事業者所有の相関ポリシーに基づく"
    "参考用の DRAFT アラーム相関エビデンスキューであり、根本原因・障害判定・復旧措置・アラーム抑制・"
    "チケット変更を確定するものではありません。相関グループはすべて候補であり、採否・優先度・現地作業"
    "などの最終判断は、必ず認可された NOC の人手レビュー（HumanApprovalGate）を経てください。本エージェント"
    "はライブテレメトリに接続せず、助言用のエビデンスキュー草案を生成するのみで、ネットワーク機器の操作は"
    "行いません。"
)

_CITATION_INCOMPLETE_MSG = (
    "相関エビデンスキューの一部グループに検証可能な出典（provenance）または相関ルール ID/バージョンが"
    "確認できなかったため、根拠不十分なキュー草案の提示を差し控えました。各アラームに認可済みシステムの"
    "参照（source）を付与のうえ再実行してください。"
)
_NEEDS_REVIEW_NOTE = (
    "Grounding could not be verified for every correlation group; the draft evidence queue is withheld "
    "pending valid provenance / correlation-rule versioning and authorized NOC review."
)

# S-3 defense-in-depth: re-redact secrets / contact info / element-or-company names that could leak into any
# free-text field of the queue (applied to the whole serialized report before it becomes the output envelope).
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}|\d{12})\b")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+81[-\s]?\d{1,4}|0\d{1,4})[-\s]?\d{1,4}[-\s]?\d{3,4})(?![\d.])")
# Company names: an English name run ending in a corporate suffix, or a Japanese company form.
_COMPANY = re.compile(
    r"(?:[A-Z][A-Za-z0-9&.\-]*\s){1,4}(?:Inc|Corp|Corporation|Ltd|LLC|LLP|GmbH|PLC|K\.?K|KK)\b\.?"
    r"|[^\s\"',]{1,24}(?:株式会社|有限会社|合同会社)"
    r"|(?:株式会社|有限会社|合同会社)[^\s\"',]{1,24}"
)
# Injection markers neutralised (defense-in-depth) if any survived into the composed queue.
_INJECTION = re.compile(
    r"(?i)(ignore all previous|ignore previous|disregard the above|system prompt|you are now|"
    r"###system|<\|im_start\|>)"
)
_REDACTORS = (_SECRET, _EMAIL, _PHONE, _COMPANY, _INJECTION)


def _sanitize_report(report: dict[str, Any]) -> dict[str, Any]:
    """Serialize → redact secret/contact/company patterns + neutralise injection → deserialize."""
    text = json.dumps(report, ensure_ascii=False)
    for pattern in _REDACTORS:
        text = pattern.sub("[REDACTED]", text)
    return cast(dict[str, Any], json.loads(text))


class PostProcessNode(FunctionNode):
    """Verify per-group citations, sanitize the queue, append the DRAFT advisory disclaimer, emit audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check: the DRAFT advisory disclaimer must be present in the output envelope.

        SDK 1.0.0 contract: receives the **result dict from `execute()`**; returns the (possibly filtered)
        result. MAY raise to block an output missing the mandatory disclaimer.
        """
        out = result.get("formatted_output", "")
        if out and "参考" not in out and "DRAFT" not in out:
            raise ValueError("S-3: DRAFT advisory disclaimer missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = _sanitize_report(json.loads(state.get("result", "{}") or "{}"))

        grounded = report.get("status_kind") == "alarm_correlation_evidence_queue"
        citations = report.get("citations", [])
        groups = report.get("correlation_groups", [])
        # S-3 per-entry authoritative correspondence: every correlation group must carry BOTH its own
        # complete local grounding (citation_complete + rule id + version + at least one source) AND have
        # every source it cites represented in the authoritative top-level ``citations`` list. A group whose
        # top-level citation is absent — or that only carries a source belonging to a different group (not
        # present at the top level) — must fail closed; a non-empty top-level list alone is not sufficient.
        top_level_sources = {c for c in citations if c}
        citation_complete = (not grounded) or (
            bool(citations)
            and bool(groups)
            and all(
                g.get("citation_complete")
                and g.get("correlation_rule_id")
                and g.get("correlation_rule_version")
                and g.get("citations")
                and all(src in top_level_sources for src in g.get("citations", []))
                for g in groups
            )
        )

        # S-3 fail-closed: an ungrounded queue (any group missing a verifiable citation or rule id/version)
        # is never presented. Degrade to a safe needs-review answer (SUCCESS + error_code), withhold the
        # queue body, and still run the disclaimer + terminal S-4 audit.
        if grounded and not citation_complete:
            error_code = state.get("error_code") or "CITATION_INCOMPLETE"
            blocked: dict[str, Any] = {
                "status_kind": "needs_review",
                "scope": report.get("scope"),
                "queue_summary": {},
                "correlation_groups": [],  # incomplete queue body withheld
                "noc_review": {"required": True, "status": "pending_noc_review", "note": _NEEDS_REVIEW_NOTE},
                "citations": [],
                "citation_complete": False,
                "message": _CITATION_INCOMPLETE_MSG,
                "limitations": report.get("limitations", []),
                "disclaimer": _DISCLAIMER,
            }
            emit_trace_event(
                "post_process.citation_blocked",
                {"group_count": len(groups), "error_code": error_code},
                state,
            )
            return {
                "formatted_output": json.dumps(blocked, ensure_ascii=False),
                "disclaimer": _DISCLAIMER,
                "audit_logged": True,
                "error_code": error_code,
                "status": AgentStatus.SUCCESS.value,
            }

        noc_review = report.get("noc_review", {"required": False, "status": "not_required"})

        formatted = {
            "status_kind": report.get("status_kind"),
            "scope": report.get("scope"),
            "queue_summary": report.get("queue_summary", {}),
            "correlation_groups": groups,
            "noc_review": noc_review,
            "citations": citations,
            "citation_complete": citation_complete,
            "message": report.get("message"),
            # stable codes only (see service.py); the explanation is in `message`
            "limitations": report.get("limitations", []),
            "disclaimer": _DISCLAIMER,
        }
        emit_trace_event(
            "post_process.complete",
            {
                "status_kind": report.get("status_kind"),
                "group_count": len(groups),
                "review_required": noc_review.get("required", False),
                "citation_complete": citation_complete,
                "error_code": state.get("error_code"),
                "limitations": report.get("limitations", []),
            },
            state,
        )
        return {
            "formatted_output": json.dumps(formatted, ensure_ascii=False),
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "status": AgentStatus.SUCCESS.value,
        }
