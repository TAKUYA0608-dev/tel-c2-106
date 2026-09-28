# TEL-C2-106 — Telecom Alarm-Correlation Evidence Queue Agent

> **Category**: Cat 2 (domain workflow (a job to be done))
> **Industry**: Telecom

## Overview

Builds an evidence queue of correlated network alarms for NOC triage. Given a JSON payload with a scope, period, topology adjacencies, a correlation policy (time window, version) and an approved alarm export (event id, timestamp, element, alarm type, free text, source system), the agent validates and normalises the alarms, groups them by topology adjacency, time-window proximity and alarm-type co-occurrence, maps each member's free text onto the policy rule vocabulary with an allow-list match, attaches an uncertainty label and supporting event ids per group, and returns correlation_groups with citations, a queue_summary, a NOC-confirmation marker and a draft disclaimer. Grouping and interpretation are deterministic; no LLM is used. Event and element identifiers are replaced by opaque tokens and operator names redacted, alarm free text is treated strictly as data (prompt-like content routes its group to a person instead of being followed), a group missing a verifiable source citation or rule version causes the queue to be withheld, and every group awaits NOC confirmation — the agent never accepts or rejects a group. The rule vocabulary shipped here is a small seeded sample.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later (`requires-python = ">=3.11"`) |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Known limitations

**Element names that look like personal names are masked by the platform.** AGENTIC STAR's personal-data
protection runs before this template's code and replaces e-mail addresses, phone numbers and runs of two or
more Title-Case words with `[MASKED]`; the template cannot switch it off. Two different elements such as
`Shinjuku Hub` and `Osaka Core` then arrive as the same value, so the same-element / adjacency join can bundle
unrelated sites. Every group that includes such an element is marked `needs_review` (the group carries
`limitation: "ELEMENT_ID_MASKED"` and its rationale says why), the queue's `limitations` is
`["ELEMENT_ID_MASKED"]`, and `message` says how many groups are affected. Measured on AgentCore 1.0.3:

| Element ids | Without masking | On the platform |
|---|---|---|
| `NE.Core.A`, `NE.Edge.B` (adjacent) | 1 group, high | 1 group, high, `limitations: []` |
| `Shinjuku Hub`, `Osaka Core` (not adjacent) | no correlation | 1 group, needs_review — `ELEMENT_ID_MASKED` |
| `NE.Core.A` and `Osaka Core` (not adjacent) | no correlation | 1 group, needs_review — `ELEMENT_ID_MASKED` |

Masking only ever merges element ids, never splits them, so a "no correlation" result and groups made only
of unmasked ids are not affected. Use hostnames or dotted / hyphenated ids (`TKY-SJK-CR01`, `NE.Core.A`) to
keep grouping exact.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
