"""Marketplace Pod entrypoint — one-shot process, referenced by the Dockerfile CMD.

Template-owned (the scaffold ships a blueprint with placeholders): it targets
this template's own Graph rather than the blueprint's ChatAgent.

The runner passes `config` straight through to `agent_cls(config=...)`, so
`config/config.yaml` reaches the agent instead of the agent being constructed
bare and running on framework defaults without saying so. Both wheels a
deployment can carry support this: the vendored 1.0.1 the CoE deploy tool
installs, and agentcore 1.0.2 from the package registry (measured on both).

The import of `shared.bootstrap` stays inside `main()`: it ships in the wheel
only with the `[marketplace]` extra, so a local environment installed without
that extra can still import this module (test collection must not depend on the
extra being present).
"""
from pathlib import Path

from framework.utils.config_loader import load_config

from src.graph.graph import Graph as Agent

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "config.yaml"


def load_this_agents_config() -> dict:
    """Load this template's own ``config/config.yaml``, or ``{}`` if absent.

    This is what agentcore 1.0.2's ``load_agent_config`` does, written out
    rather than called: that helper does not exist in the vendored 1.0.1 wheel
    the CoE deploy tool installs, so importing it makes this module unimportable
    in that image — a container that builds and pushes cleanly and then dies on
    Pod start. ``load_config`` is present in both wheels (measured), so the
    entry point stays valid whichever one the image is built with.
    """
    return load_config(str(CONFIG_PATH)) if CONFIG_PATH.exists() else {}


def wait_for_egress_before_start() -> bool:
    """Wait for the envoy egress sidecar, and report whether it was awaited.

    The sidecar (HTTPS_PROXY=127.0.0.1:15001) can begin accepting connections
    after this container's process starts, so any outbound call issued in that
    window never leaves the pod. Which wheel this template runs
    on is decided when the image is built, not here, and the two differ:
    agentcore 1.0.2's ``marketplace_app`` awaits this itself, while the vendored
    1.0.1 the CoE deploy tool installs does not. Keeping the wait template-side
    is correct for both — on 1.0.2 it is a second call that the sidecar has
    already satisfied and which returns at once; on 1.0.1 it is the only one.

    Fail-open by construction: if the helper is absent or raises, the run
    continues rather than aborting, because the worst case is exactly the
    pre-wait behaviour the fleet already ships. ``wait_for_egress`` bounds
    itself at 15s and logs its own warning on timeout.
    """
    import asyncio

    try:
        from agenticstar_platform import wait_for_egress
    except ImportError:
        return False
    try:
        asyncio.run(wait_for_egress())
    except Exception:  # noqa: BLE001 - see the fail-open contract above
        return False
    return True


def main() -> None:
    wait_for_egress_before_start()
    # Imported here, not at module scope — see the module docstring.
    from shared.bootstrap.marketplace_app import run_agent_marketplace

    run_agent_marketplace(
        Agent,
        agent_name="tel-c2-106",
        # Same secret-provisioning namespace as src/api/server.py, so both entry
        # points resolve through one chain. EnvProvider reads os.environ
        # unscoped, so the registered Marketplace env vars are unaffected.
        namespace="agent1000/agent-templates",
        config=load_this_agents_config(),
    )


if __name__ == "__main__":
    main()
