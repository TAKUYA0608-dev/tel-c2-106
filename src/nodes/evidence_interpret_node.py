"""TEL-C2-106 — inner workflow step 3: evidence_interpret.

Bounded interpretation (deterministic; no LLM) + **pre-interpretation containment**: for each candidate
group, map every member's alarm free-text to the policy rule vocabulary (allow-list keyword match), select
and cite supporting event IDs, and attach an uncertainty label (high / medium / needs_review) on partial
match / maintenance-overlap ambiguity / missing topology / contained (prompt-like) content. Composes the
**AlarmCorrelationEvidenceQueue** deliverable with per-group citations.

- 0 groups with alarms present → grounded-but-uncited ``no_correlations`` (no fabricated grouping).
- 0 alarms / rejected → out-of-scope safe answer (``citations=[]``).

Untrusted alarm free-text is treated strictly as **data**: embedded prompt-like content is never followed;
a member carrying non-allow-listed / instruction-like text routes its group to needs-review (contained).
This is a distinct layer from S-2 (identifier masking) and S-3 (output sanitisation).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import ELEMENT_ID_MASKED, AlarmCorrelationService
from src.utils.audit import emit_trace_event

_OUT_OF_SCOPE = (
    "分析可能なアラーム相関データが入力に見つかりませんでした。"
    "alarms 配列に event_id / ts / element / alarm_type / text を含む JSON と、必要に応じて "
    "topology / maintenance_windows / correlation_policy をご指定ください。"
)
_ELEMENT_ID_MASKED_MSG = (
    "{n} 件の相関グループに、プラットフォームの個人データ保護により本エージェントの処理前に [MASKED] へ置換された"
    "設備 ID が含まれます（人名に見える英字の語の並び・メールアドレス・電話番号など）。別々の設備が同じ値として"
    "届くため、これらのグループは同一設備・隣接の判定を確認できず needs_review としています。ホスト名やドット／"
    "ハイフン区切りの ID（例: NE.Core.A）を使うと、設備を区別して判定できます。"
)
_NO_CORRELATIONS = (
    "供給されたアラームからは、相関ポリシー（topology 隣接 + 時間窓 + アラーム種別の共起）に基づく"
    "候補相関グループは形成されませんでした。個別アラームの相関エビデンスはありません。"
)


class EvidenceInterpretNode(FunctionNode):
    """Interpret candidate groups (bounded, contained), cite events, label uncertainty, compose the queue."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        slots = json.loads(state.get("validated_input") or "{}")

        # 0-alarm / rejected → out-of-scope safe answer (no fabricated evidence).
        if state.get("error_code") or state.get("alarm_count", 0) == 0:
            emit_trace_event("evidence_interpret.safe", {"reason": state.get("error_code") or "no_alarms"}, state)
            report: dict[str, Any] = {
                "status_kind": "out_of_scope",
                "message": _OUT_OF_SCOPE,
                "scope": self._scope(slots),
                "queue_summary": {},
                "correlation_groups": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        groups = json.loads(state.get("candidate_groups") or "[]")
        alarm_count = int(state.get("alarm_count", 0))

        # 0 groups with alarms present → grounded-but-uncited no_correlations (not a fabricated grouping).
        if not groups:
            emit_trace_event("evidence_interpret.no_correlations", {"alarm_count": alarm_count}, state)
            report = {
                "status_kind": "no_correlations",
                "message": _NO_CORRELATIONS,
                "scope": self._scope(slots),
                "queue_summary": {
                    "alarm_count": alarm_count,
                    "group_count": 0,
                    "uncertainty_distribution": {"high": 0, "medium": 0, "needs_review": 0},
                    "groups_needing_review": [],
                },
                "correlation_groups": [],
                "citations": [],
            }
            return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

        signals_by_evt = {s["event_id"]: s for s in json.loads(state.get("normalized_alarms") or "[]")}
        policy = AlarmCorrelationService.resolve_policy(slots.get("correlation_policy"))
        rules = policy["rules"]
        topology = slots.get("topology") if isinstance(slots.get("topology"), list) else []
        maintenance = slots.get("maintenance_windows") if isinstance(slots.get("maintenance_windows"), list) else []
        maintenance_elements: Any = {m.get("element") for m in maintenance if isinstance(m, dict) and m.get("element")}
        topology_seen: set[str] = set()
        for edge in topology:
            if isinstance(edge, dict):
                for key in ("element_a", "element_b", "a", "b"):
                    if edge.get(key):
                        topology_seen.add(edge[key])

        # Element surrogates the platform masked (recorded at S-1): groups joining on them are unverified.
        masked = frozenset(slots.get("masked_elements") or [])
        interpreted: list[dict[str, Any]] = []
        citations: list[str] = []
        for g in groups:
            g = dict(g)
            g["_members"] = [signals_by_evt[eid] for eid in g.get("member_event_ids", []) if eid in signals_by_evt]
            evidence = AlarmCorrelationService.interpret(g, rules, maintenance_elements, topology_seen, masked)
            interpreted.append(evidence)
            for c in evidence["citations"]:
                if c not in citations:
                    citations.append(c)

        summary = AlarmCorrelationService.queue_summary(interpreted, alarm_count)
        masked_groups = sum(1 for g in interpreted if g.get("limitation") == ELEMENT_ID_MASKED)
        report = {
            "status_kind": "alarm_correlation_evidence_queue",
            "scope": self._scope(slots),
            "queue_summary": summary,
            "correlation_groups": interpreted,
            "citations": citations,
            # stable codes only; the explanation is in `message`
            "limitations": [ELEMENT_ID_MASKED] if masked_groups else [],
        }
        if masked_groups:
            report["message"] = _ELEMENT_ID_MASKED_MSG.format(n=masked_groups)
        emit_trace_event(
            "evidence_interpret.complete",
            {
                "group_count": len(interpreted),
                "citation_count": len(citations),
                "uncertainty_distribution": summary["uncertainty_distribution"],
                "element_id_masked_groups": masked_groups,
            },
            state,
        )
        return {"result": json.dumps(report, ensure_ascii=False), "status": AgentStatus.SUCCESS.value}

    @staticmethod
    def _scope(slots: dict[str, Any]) -> dict[str, Any]:
        return {"scope": slots.get("scope"), "period": slots.get("period")}
