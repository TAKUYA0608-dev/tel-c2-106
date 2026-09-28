"""TEL-C2-106 — the Marketplace runner's output contract.

The runner rejects a successful invocation whose output is missing (verified on
a deployed Pod), and a degraded run produces no artefact. These pin
both halves of the rule: a success always carries something, a refusal still
carries nothing.
"""

from src.graph.graph import Graph


def _agent():
    return Graph()


class TestDegradedRunCarriesOutput:
    def test_degraded_success_is_not_empty(self):
        out = _agent().get_output({"status": "success", "error_code": "PROBE_DEGRADED"})
        assert out["output"], "a successful run with an empty output is rejected by the runner"
        assert "PROBE_DEGRADED" in out["output"]

    def test_normal_result_is_untouched(self):
        out = _agent().get_output({"status": "success", "formatted_output": "the real answer"})
        assert out["output"] == "the real answer"


class TestRefusalPublishesNothing:
    def test_error_status_keeps_output_empty(self):
        out = _agent().get_output({"status": "error", "error_code": "INJECTION_DETECTED"})
        assert not out["output"], f"a refused request published output: {out['output']!r}"
