"""Unit tests — Marketplace entrypoint wiring.

Pins the seam the entry point exists for: `config/config.yaml` reaches the
agent, rather than the agent being constructed bare and the deployment running
on framework defaults without saying so.

The regression this file exists to catch is a *silent* one — an entry point that
still starts, still serves, and quietly ignores this template's configuration.
So the config assertions below pin the loaded values, not the type: asserting
`isinstance(config, dict)` would pass against a hard-coded `config={}`, which is
exactly the regression.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import cli

REPO_ROOT = Path(cli.__file__).resolve().parent


def _capture(monkeypatch, *, egress=None):
    """Run cli.main() with the runner and the egress helper stood in for.

    ``egress`` replaces the stand-in module's ``wait_for_egress`` so the
    fail-open cases can be exercised; by default it records its own turn in
    ``seen["order"]``. The helper is always stood in — left real, it would try
    to reach a sidecar that does not exist here and block every test for its
    full 15s timeout.
    """
    seen = {"order": []}

    async def _fake_wait():
        seen["order"].append("egress")
        return True

    platform = types.ModuleType("agenticstar_platform")
    if egress != "absent":
        platform.wait_for_egress = _fake_wait if egress is None else egress
    monkeypatch.setitem(sys.modules, "agenticstar_platform", platform)

    def _fake_run(agent_cls, **kwargs):
        seen["order"].append("runner")
        seen["agent_cls"] = agent_cls
        seen.update(kwargs)
        return None

    module = types.ModuleType("shared.bootstrap.marketplace_app")
    module.run_agent_marketplace = _fake_run
    monkeypatch.setitem(sys.modules, "shared.bootstrap.marketplace_app", module)
    cli.main()
    return seen


class TestEntrypointWiring:
    def test_the_loaded_config_yaml_reaches_the_runner(self, monkeypatch):
        expected = cli.load_this_agents_config()
        assert expected, "config/config.yaml must load non-empty, or this test proves nothing"
        assert _capture(monkeypatch)["config"] == expected

    def test_the_configured_values_arrive(self, monkeypatch):
        # Pinned independently of the loader: the equality above compares the
        # entry point against the same function it calls, so a loader that began
        # returning {} would satisfy it while the deployment silently ran on
        # framework defaults. These are this repository's own committed values.
        config = _capture(monkeypatch)["config"]
        assert config["max_retry"] == 3
        assert config["timeout_seconds"] == 30

    def test_identity_and_namespace(self, monkeypatch):
        seen = _capture(monkeypatch)
        assert seen["agent_name"] == "tel-c2-106"
        # Same value src/api/server.py passes to secrets_factory.
        assert seen["namespace"] == "agent1000/agent-templates"

    def test_the_agent_class_is_the_one_the_registry_declares(self, monkeypatch):
        # config/agent.yaml declares this template's entry point; the Pod entry
        # point must hand the runner that same class, not a similarly-named one.
        from src.graph.graph import Graph

        assert _capture(monkeypatch)["agent_cls"] is Graph


class TestEgressWait:
    """The sidecar race is only closed if the wait happens
    *before* the runner starts. Which wheel the image carries is a deploy-time
    choice — the vendored 1.0.1 the CoE tool installs does not await it, 1.0.2
    does — so the wait is owned here and must hold on both.
    """

    def test_the_wait_runs_before_the_runner(self, monkeypatch):
        assert _capture(monkeypatch)["order"] == ["egress", "runner"]

    def test_the_run_continues_when_the_helper_is_absent(self, monkeypatch):
        # Older platform builds may not export it; a missing helper must not
        # cost the run, only the protection.
        assert _capture(monkeypatch, egress="absent")["order"] == ["runner"]

    def test_the_run_continues_when_the_wait_fails(self, monkeypatch):
        async def _boom():
            raise RuntimeError("sidecar probe failed")

        assert _capture(monkeypatch, egress=_boom)["order"] == ["runner"]
