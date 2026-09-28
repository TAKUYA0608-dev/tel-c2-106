# TEL-C2-106 — Integration: the real invoke() path, with the platform's S-2 masking active.
#
# The platform's input gate runs before this template's code and replaces e-mail / phone values and any
# run of two or more Title-Case words with "[MASKED]". Element identifiers are tokenised by hashing, so two
# unrelated elements that both arrive as "[MASKED]" get the same surrogate. Measured on AgentCore 1.0.3:
# alarms on "Shinjuku Hub" and "Osaka Core" (not adjacent) came back as one "high" group instead of
# no correlation. These tests call Graph().invoke() as a VERIFIED_EXTERNAL caller, so the masking is part
# of every run.

import json
from collections.abc import Iterator
from typing import Any

import framework.nodes.function_node as function_node
import pytest
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph

_TWO_SITES: dict[str, Any] = {
    "scope": "kanto_noc",
    "period": "2026-Q4",
    "topology": [
        {"element_a": "Shinjuku Hub", "element_b": "NE.Edge.1"},
        {"element_a": "Osaka Core", "element_b": "NE.Edge.9"},
    ],
    "alarms": [
        {
            "event_id": "a1",
            "ts": 1000,
            "element": "Shinjuku Hub",
            "alarm_type": "los",
            "text": "optical los transport link",
            "source": "ems:a1",
        },
        {
            "event_id": "a2",
            "ts": 1060,
            "element": "Osaka Core",
            "alarm_type": "link_down",
            "text": "transport link down optical los",
            "source": "ems:a2",
        },
    ],
}
# Mixed case: one member element is a dotted id (not masked), the other a Title-Case name (masked). The
# topology links the dotted element to "Shinjuku Hub" only; "Osaka Core" is somewhere else entirely.
_MIXED: dict[str, Any] = {
    "scope": "kanto_noc",
    "period": "2026-Q4",
    "topology": [{"element_a": "NE.Core.A", "element_b": "Shinjuku Hub"}],
    "alarms": [
        {
            "event_id": "m1",
            "ts": 1000,
            "element": "NE.Core.A",
            "alarm_type": "los",
            "text": "optical los transport link",
            "source": "ems:m1",
        },
        {
            "event_id": "m2",
            "ts": 1060,
            "element": "Osaka Core",
            "alarm_type": "link_down",
            "text": "transport link down optical los",
            "source": "ems:m2",
        },
    ],
}
_DOTTED: dict[str, Any] = {
    "scope": "global_noc",
    "period": "2026-Q4",
    "topology": [{"element_a": "NE.Core.A", "element_b": "NE.Edge.B"}],
    "alarms": [
        {
            "event_id": "e1",
            "ts": 1000,
            "element": "NE.Core.A",
            "alarm_type": "los",
            "operator_name": "Confidential Operator",
            "text": "loss of signal optical transport link port down",
            "source": "netcool:e1",
        },
        {
            "event_id": "e2",
            "ts": 1120,
            "element": "NE.Edge.B",
            "alarm_type": "link_down",
            "text": "transport link down fiber optical los",
            "source": "netcool:e2",
        },
    ],
}
_CODE = "ELEMENT_ID_MASKED"


def _ctx() -> InvocationContext:
    return InvocationContext(caller_id="integration-test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _queue(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = Graph().invoke(json.dumps(payload, ensure_ascii=False), ctx=_ctx())
    assert str(out.get("status")).lower().endswith("success"), out.get("status")
    body = out["output"]
    queue: dict[str, Any] = json.loads(body) if isinstance(body, str) else body
    return queue


def _platform_view(text: str) -> str:
    """What the template's nodes receive: the text after the framework's S-2 masking."""
    return str(function_node.mask_pii(text, function_node.detect_pii(text)))


@pytest.fixture
def no_platform_masking(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment whose S-2 pass does not mask (the template's original assumption)."""
    monkeypatch.setattr(function_node, "detect_pii", lambda text: [])
    yield


class TestPlatformMaskedElements:
    def test_collapsed_elements_never_yield_a_high_confidence_group(self) -> None:
        view = _platform_view(json.dumps(_TWO_SITES))
        assert "Shinjuku Hub" not in view and "Osaka Core" not in view, "precondition: both names masked"
        q = _queue(_TWO_SITES)
        assert q["queue_summary"]["uncertainty_distribution"]["high"] == 0, q
        assert q["correlation_groups"], q
        assert all(g["uncertainty_label"] == "needs_review" for g in q["correlation_groups"])
        assert all(g["limitation"] == _CODE for g in q["correlation_groups"])
        assert all(_CODE in g["advisory_rationale"] for g in q["correlation_groups"])
        assert q["limitations"] == [_CODE], q
        assert "1 件の相関グループ" in q["message"], q["message"]

    def test_an_unmasked_member_plus_a_masked_member_is_not_high(self) -> None:
        # Without masking these two alarms do not correlate; with it, "Osaka Core" arrives as the same
        # value as "Shinjuku Hub" and looks adjacent to the unmasked NE.Core.A.
        view = _platform_view(json.dumps(_MIXED))
        assert "NE.Core.A" in view and "Osaka Core" not in view, "precondition: one member masked, one not"
        q = _queue(_MIXED)
        groups = q["correlation_groups"]
        assert len(groups) == 1 and len(groups[0]["member_elements"]) == 2, q
        assert groups[0]["uncertainty_label"] == "needs_review", groups[0]
        assert q["limitations"] == [_CODE], q


class TestUnmaskedElementsAreUnchanged:
    def test_dotted_element_ids_are_unaffected(self) -> None:
        q = _queue(_DOTTED)  # operator_name is masked too, but that field is dropped anyway
        assert [g["uncertainty_label"] for g in q["correlation_groups"]] == ["high"]
        assert "limitation" not in q["correlation_groups"][0]
        assert q["limitations"] == []
        assert q["message"] is None

    @pytest.mark.usefixtures("no_platform_masking")
    def test_without_platform_masking_distinct_sites_do_not_correlate(self) -> None:
        for payload in (_TWO_SITES, _MIXED):
            q = _queue(payload)
            assert q["status_kind"] == "no_correlations", q
            assert q["limitations"] == []
