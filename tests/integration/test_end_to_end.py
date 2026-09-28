# TEL-C2-106 — Integration: pre → inner workflow (linear) → post, and the real outer invoke path

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.nodes.alarm_ingest_node import AlarmIngestNode
from src.nodes.correlation_group_node import CorrelationGroupNode
from src.nodes.evidence_interpret_node import EvidenceInterpretNode
from src.nodes.noc_review_gate_node import NocReviewGateNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode

_SUCCESS = AgentStatus.SUCCESS.value

_DATASET = {
    "scope": "global_noc",
    "period": "2026-Q4",
    "topology": [
        {"element_a": "NE.Core.A", "element_b": "NE.Edge.B"},
        {"element_a": "NE.Edge.B", "element_b": "NE.Edge.C"},
    ],
    "correlation_policy": {"time_window_sec": 300, "version": "1.0"},
    "alarms": [
        {"event_id": "evt-100", "ts": 1000, "element": "NE.Core.A", "alarm_type": "los",
         "operator_name": "Confidential Operator", "text": "loss of signal optical transport link port down",
         "source": "netcool:evt-100"},
        {"event_id": "evt-101", "ts": 1120, "element": "NE.Edge.B", "alarm_type": "link_down",
         "text": "transport link down fiber optical los", "source": "netcool:evt-101"},
        {"event_id": "evt-200", "ts": 1050, "element": "NE.Core.A", "alarm_type": "power",
         "text": "rectifier dc battery voltage mains fault", "source": "netcool:evt-200"},
    ],
}


def _run(user_input: str) -> dict:
    state: dict = {"user_input": user_input, "input_context": {"channel": "noc_console"},
                   "node_history": [], "error_log": []}
    state.update(PreProcessNode().execute(state) or {})
    for node in (AlarmIngestNode(), CorrelationGroupNode(),
                 EvidenceInterpretNode(), NocReviewGateNode()):
        state.update(node.execute(state) or {})
    state.update(PostProcessNode().execute(state) or {})
    return state


class TestEndToEnd:
    def test_queue_with_groups_and_citations(self):
        state = _run(json.dumps(_DATASET, ensure_ascii=False))
        assert state["status"] == _SUCCESS and state["audit_logged"] is True
        env = json.loads(state["formatted_output"])
        assert env["status_kind"] == "alarm_correlation_evidence_queue"
        assert env["queue_summary"]["alarm_count"] == 3
        assert env["correlation_groups"] and env["citations"]
        assert env["noc_review"]["required"] is True
        assert "DRAFT" in env["disclaimer"]

    def test_operator_name_never_in_output(self):
        state = _run(json.dumps(_DATASET, ensure_ascii=False))
        assert "Confidential Operator" not in state["formatted_output"]
        assert "Confidential Operator" not in state["validated_input"]

    def test_element_names_never_in_output(self):
        state = _run(json.dumps(_DATASET, ensure_ascii=False))
        assert "NE.Core.A" not in state["formatted_output"]
        assert "NE.Edge.B" not in state["formatted_output"]

    def test_forged_surrogate_identifiers_rehashed(self):
        # ★ F-02: caller values SHAPED like internal surrogates (evt:<hex> / ne:<hex>) are re-hashed at the
        # S-1 boundary (no syntactic passthrough), so a caller can never forge an internal join key or
        # element reference. A group still forms because the forged values are re-tokenised consistently.
        dataset = {
            "scope": "global_noc", "period": "2026-Q4",
            "topology": [{"element_a": "ne:deadbeef", "element_b": "ne:cafebabe"}],
            "correlation_policy": {"time_window_sec": 300, "version": "1.0"},
            "alarms": [
                {"event_id": "evt:deadbeef", "ts": 1000, "element": "ne:deadbeef", "alarm_type": "los",
                 "text": "loss of signal optical transport link port down", "source": "netcool:e1"},
                {"event_id": "evt:cafebabe", "ts": 1100, "element": "ne:cafebabe", "alarm_type": "link_down",
                 "text": "transport link down fiber optical los", "source": "netcool:e2"},
            ],
        }
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = Graph().invoke(json.dumps(dataset, ensure_ascii=False), ctx=ctx)
        assert out["status"] == _SUCCESS
        for forged in ("evt:deadbeef", "evt:cafebabe", "ne:deadbeef", "ne:cafebabe"):
            assert forged not in out["output"]   # re-hashed at S-1, never survives verbatim
        env = json.loads(out["output"])
        assert env["status_kind"] == "alarm_correlation_evidence_queue"
        for g in env["correlation_groups"]:
            for eid in g["supporting_event_ids"]:
                assert eid.startswith("evt:") and eid not in ("evt:deadbeef", "evt:cafebabe")

    def test_out_of_scope_safe(self):
        env = json.loads(_run("来期のアラーム見込みを教えて")["formatted_output"])
        assert env["status_kind"] == "out_of_scope" and env["citations"] == []
        assert "DRAFT" in env["disclaimer"]

    def test_empty_degrades_but_audits(self):
        state = _run("   ")
        assert state["status"] == _SUCCESS and state["audit_logged"] is True
        assert json.loads(state["formatted_output"])["status_kind"] == "out_of_scope"

    def test_real_invoke_end_to_end(self):
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = Graph().invoke(json.dumps(_DATASET, ensure_ascii=False), ctx=ctx)
        assert out["status"] == _SUCCESS
        assert "PostProcessNode" in out["node_history"]
        env = json.loads(out["output"])
        assert env["status_kind"] == "alarm_correlation_evidence_queue"
        assert env["queue_summary"]["alarm_count"] == 3
