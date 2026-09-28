"""TEL-C2-106 — deterministic domain services (no framework imports, no LLM).

AlarmCorrelationService: normalizes a supplied approved, de-identified alarm export (+ topology /
maintenance-window extracts + a carrier-owned correlation policy) into a canonical signal set, groups
policy-related alarms by deterministic correlation (topology-adjacency join + time-window proximity +
alarm-type co-occurrence), performs bounded free-text-to-policy-vocabulary interpretation (allow-list
match), attaches an uncertainty label, and synthesizes an AlarmCorrelationEvidenceQueue for NOC review.

Everything here is deterministic and auditable (union-find grouping + threshold banding + allow-list
keyword/shingle match + keyed policy composition) — there is **no LLM**. Alarms are keyed by an opaque,
unconditionally tokenized ``event_id`` / ``element``; the raw alarm free-text and topology detail are never
carried into the queue output (derived rule ids / tokenized ids / vocab-match rationale / counts only), and
the S-3 output gate re-redacts any that leak. Seeded correlation policy (rule vocabulary + thresholds) is
overridable by CoE without touching node logic. The pure correlation computation (topology join /
time-window / count) is carved into ``shared/tools/correlation`` reference functions (§2-3 of the proposal);
the Agent value concentrates in free-text interpretation + uncertainty + advisory synthesis.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Two SEPARATE concerns — do not conflate them:
#   (1) PRIVACY (safe_event_id / safe_element): every caller identifier (event_id / element / ne_id) is
#       UNCONDITIONALLY tokenized to a deterministic opaque surrogate so PII / topology detail (even a bare
#       name like ``NE.Core.1`` / ``Alice``, no spaces/symbols) can never reach a citation or the output.
#       Tokenization is UNCONDITIONAL — there is NO syntactic passthrough: a caller value merely *shaped*
#       like a surrogate (``evt:deadbeef`` / ``ne:deadbeef``) is re-hashed, never trusted, so it can never
#       forge an internal join key. Identifiers are resolved exactly ONCE at the S-1 boundary (pre_process);
#       downstream trusts that single resolution verbatim (see ``normalize``). Tokenizing is a privacy
#       measure — it does NOT assert the value is authorized/verifiable.
#   (2) PROVENANCE (resolve_provenance): a caller ``source`` becomes a grounded CITATION only when it is
#       resolvable against the authorized provenance registry (names a trusted EMS/OSS/fault-management
#       system of record). Any other free text (an element name, ``unknown``, a fabricated value, or a
#       value merely SHAPED like a surrogate ``src:1a2b3c4d``) is NOT verifiable provenance → it yields NO
#       citation → S-3 blocks the queue as CITATION_INCOMPLETE (fail-closed). "Tokenized" is never
#       sufficient for a citation; the value must first pass provenance validation.
_SAFE_TOKEN = re.compile(r"^[a-z0-9_\-]{1,32}$")

# Authorized provenance registry: the systems of record a telecom carrier trusts as verifiable alarm/event
# data sources (EMS / OSS / NMS / fault-management platforms). A caller ``source`` is accepted as a
# grounded citation ONLY when its leading namespace names one of these (the "trusted context"). This is the
# deploying carrier's / CoE's registry — overridable without touching node logic; it is a SEMANTIC allowlist
# of authorized systems, not a syntactic character class.
AUTHORIZED_PROVENANCE_SYSTEMS = frozenset(
    {
        "ems",
        "oss",
        "nms",
        "fm",
        "fault_management",
        "alarm_feed",
        "event_feed",
        "assurance",
        "netcool",
        "netact",
        "enm",
        "u2000",
        "nsp",
        "spo",
        "watchtower",
        "ensemble",
        "system_of_record",
        "sor",
        "authorized_feed",
        "data_warehouse",
        "dwh",
    }
)

# Correlation-policy version stamped onto every group (fixed onto the evidence for traceability; policy
# updates are change-controlled — human-engineer MR + expert review — never delegated to operations).
DEFAULT_CORRELATION_POLICY_VERSION = "seed-1.0"

# ── seeded carrier-owned correlation policy: rule id → {description, alarm_types, vocabulary} ──
# The correlation rule vocabulary (the "carrier-owned correlation policy", §12 dependency #1) — a reference
# default calibratable by CoE. ``alarm_types`` co-occurrence + ``vocabulary`` allow-list drive deterministic
# grouping and bounded free-text interpretation. Extended/overridden per deployment via the input policy.
CORRELATION_RULE_VOCABULARY: dict[str, dict[str, Any]] = {
    "power_cascade": {
        "description": "Power / rectifier fault cascading to dependent NEs on the same site",
        "alarm_types": ["power", "rectifier", "dc_low", "battery", "mains"],
        "vocabulary": ["power", "rectifier", "dc", "battery", "mains", "psu", "voltage", "supply"],
    },
    "transport_link": {
        "description": "Transport / optical link failure correlated across adjacent NEs",
        "alarm_types": ["link_down", "los", "optical", "sdh", "otn", "port_down"],
        "vocabulary": [
            "link",
            "los",
            "loss of signal",
            "optical",
            "fiber",
            "transport",
            "sdh",
            "otn",
            "port down",
            "port",
            "trunk",
        ],
    },
    "radio_access": {
        "description": "Cell / carrier outage on co-located radio units under one BBU",
        "alarm_types": ["cell_down", "carrier", "rf", "bbu", "sector"],
        "vocabulary": ["cell", "carrier", "rf", "bbu", "sector", "antenna", "coverage", "radio"],
    },
    "sync_timing": {
        "description": "Clock / sync reference loss propagating to slaved NEs",
        "alarm_types": ["sync", "clock", "gps", "timing", "holdover"],
        "vocabulary": ["sync", "clock", "gps", "timing", "holdover", "reference", "1pps"],
    },
}

# Prompt-injection / instruction markers: if a member's alarm free-text carries these, its group is
# CONTAINED (routed to needs-review), never interpreted — a distinct pre-interpretation containment layer
# separate from the S-2 caller-boundary reject and the S-3 output gate.
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
)


# ── platform masking (S-2) ─────────────────────────────────────────────────────
# The platform's S-2 personal-data pass runs before this template's code and replaces e-mail addresses,
# phone numbers and any run of two or more Title-Case words with this token; the template cannot switch it
# off. Element identifiers are tokenised by hashing, so two different elements that both arrive as
# "[MASKED]" (e.g. "Shinjuku Hub" and "Osaka Core") get the same ``ne:`` surrogate, and the same-element /
# adjacency join can bundle unrelated sites. Masking only ever merges identifiers, never splits them, so a
# group that includes such an element is unverified; a result without one is unaffected.
PLATFORM_MASK_TOKEN = "[MASKED]"
# Stable machine-readable limitation code (the human-readable explanation goes in the queue `message`).
ELEMENT_ID_MASKED = "ELEMENT_ID_MASKED"


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def safe_event_id(value: Any) -> str:
    """PRIVACY tokenize a caller event identifier to a deterministic opaque surrogate ``evt:<sha8>``.

    Caller identifiers are **always** tokenized — no syntactic passthrough — so a PII / free-text event id
    can never survive into a citation or the output, and a caller value merely *shaped* like a surrogate
    (``evt:deadbeef``) is re-hashed rather than trusted (it can never forge an internal join key). Same
    input → same surrogate (evidence / member refs stay joinable within one invocation). Privacy measure
    only; makes no claim the id is authorized.
    """
    return "evt:" + _sha8(str(value or "").strip())


def safe_element(value: Any) -> str:
    """PRIVACY tokenize a caller element / NE identifier to a deterministic opaque surrogate ``ne:<sha8>``.

    Elements are tokenized deterministically **everywhere** (alarm element, topology edges, maintenance
    windows) at the S-1 boundary so the topology-adjacency join still resolves on the surrogate while the
    raw topology detail never reaches the output. Tokenization is unconditional (no syntactic passthrough):
    a caller value merely *shaped* like a surrogate (``ne:deadbeef``) is re-hashed, never trusted.
    """
    return "ne:" + _sha8(str(value or "").strip())


def resolve_provenance(value: Any) -> str | None:
    """Resolve a **raw** caller ``source`` to a grounded, privacy-tokenized CITATION — or ``None``.

    Provenance validation (separate from privacy) and the **single** resolution point (S-1 / pre_process).
    A citation is emitted **only** when the source names an authorized system of record
    (``<authorized-namespace>[:<ref>]``). Any other value — an element name, ``unknown``, a fabricated
    value, **or a value that merely looks like a surrogate (``src:1a2b3c4d``)** — is not verifiable
    provenance and returns ``None`` so the S-3 gate blocks the queue as CITATION_INCOMPLETE (fail-closed).
    When authorized, the raw label is never used verbatim: the citation is a privacy hash (``src:<sha8>``)
    of the authorized reference. No synthetic provenance is fabricated.

    ★ Forged-surrogate defence: there is **no format-based passthrough**. A caller-supplied ``src:<hex>`` /
    ``evt:<hex>`` / ``ne:<hex>`` has a namespace that is not an authorized system of record, so it resolves
    to ``None`` — dropped here at S-1, never reaching a citation. Because provenance is resolved exactly
    once (here), the produced ``src:<sha8>`` is the trusted citation downstream and is **never** fed back
    through this function (which would, correctly, reject it), so no forged value can imitate an internal
    surrogate.
    """
    text = str(value or "").strip()
    if not text:
        return None
    namespace = text.split(":", 1)[0].strip().lower()
    if namespace not in AUTHORIZED_PROVENANCE_SYSTEMS:
        return None  # unverifiable / forged-surrogate provenance → fail-closed (no citation → needs_review)
    return "src:" + _sha8(text)


def contains_injection(text: str) -> bool:
    """True if alarm free-text carries a prompt-injection / instruction marker (containment trigger)."""
    low = (text or "").lower()
    return any(marker in low for marker in _INJECTION_MARKERS)


def _safe_alarm_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return text if _SAFE_TOKEN.match(text) else "unknown"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_epoch(value: Any) -> float | None:
    """Parse a timestamp (epoch seconds, epoch ms, or ISO-8601) to epoch seconds. Deterministic; no I/O."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v  # epoch-ms heuristic
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        v = float(text)
        return v / 1000.0 if v > 1e12 else v
    iso = text.replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


class AlarmCorrelationService:
    """Deterministic ingest/normalization, correlation grouping, bounded interpretation, and synthesis."""

    # ── policy resolution ─────────────────────────────────────────────────────
    @staticmethod
    def resolve_policy(correlation_policy: Any) -> dict[str, Any]:
        """Merge the caller-supplied correlation policy over the seeded default (rules + thresholds).

        A caller may extend/override the rule vocabulary and the time window. Missing / malformed → seeded
        defaults. ``version`` is stamped onto every group for traceability.
        """
        policy = correlation_policy if isinstance(correlation_policy, dict) else {}
        rules: dict[str, dict[str, Any]] = {k: dict(v) for k, v in CORRELATION_RULE_VOCABULARY.items()}
        supplied = policy.get("rules")
        if isinstance(supplied, dict):
            for rule_id, spec in supplied.items():
                if isinstance(rule_id, str) and _SAFE_TOKEN.match(rule_id) and isinstance(spec, dict):
                    base = dict(rules.get(rule_id, {"description": "", "alarm_types": [], "vocabulary": []}))
                    if isinstance(spec.get("alarm_types"), list):
                        base["alarm_types"] = [str(t).strip().lower() for t in spec["alarm_types"]]
                    if isinstance(spec.get("vocabulary"), list):
                        base["vocabulary"] = [str(t).strip().lower() for t in spec["vocabulary"]]
                    if isinstance(spec.get("description"), str):
                        base["description"] = spec["description"]
                    rules[rule_id] = base
        window = int(_num(policy.get("time_window_sec"), 300)) or 300
        raw_version = policy.get("version")
        version = (
            raw_version
            if (isinstance(raw_version, str) and _SAFE_TOKEN.match(raw_version.replace(".", "_")))
            else DEFAULT_CORRELATION_POLICY_VERSION
        )
        return {"rules": rules, "time_window_sec": max(1, window), "version": version}

    # ── normalization / ingest ────────────────────────────────────────────────
    @staticmethod
    def normalize(alarms: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate + canonicalize alarms into a signal set. Rows without event_id or a parseable ts drop.

        Identifiers / provenance are resolved exactly ONCE at S-1 (pre_process); normalize trusts that
        single resolution verbatim and never re-resolves. ``element`` was already tokenized to ``ne:<sha8>``
        at S-1 and is used verbatim so the topology-adjacency join (topology is likewise tokenized only at
        S-1) still resolves on the same surrogate — re-tokenizing it here would double-hash the alarm side
        only and break the join. ``event_id`` is re-tokenized unconditionally: it is a per-invocation
        internal label (member refs + citations are both built from this value, so they stay consistent),
        never joined against an externally-tokenized value, so a deterministic double-hash is harmless.
        ``source`` was already resolved to a grounded citation (``src:<sha8>``) or ``None`` by S-1 — a forged
        surrogate was dropped there; normalize never fabricates provenance. The raw alarm free-text is
        retained only length-capped + hygiened (from S-1) for bounded interpretation, never copied to output.
        """
        rules = policy["rules"]
        out: list[dict[str, Any]] = []
        for raw in alarms or []:
            if not isinstance(raw, dict):
                continue
            raw_evt = str(raw.get("event_id") or raw.get("id") or "").strip()
            ts_epoch = _parse_epoch(raw.get("ts") or raw.get("timestamp"))
            if not raw_evt or ts_epoch is None:
                continue
            # element trusted verbatim from S-1 (already ne:<sha8>); NOT re-tokenized, so the topology join
            # (topology tokenized only at S-1) still resolves on the same surrogate.
            element = str(raw.get("element") or raw.get("ne_id") or "").strip()
            alarm_type = _safe_alarm_type(raw.get("alarm_type"))
            text = str(raw.get("text") or raw.get("description") or "")
            rule_id, vocab_terms = AlarmCorrelationService._match_rule(alarm_type, text, rules)
            out.append(
                {
                    "event_id": safe_event_id(raw_evt),
                    "ts_epoch": round(ts_epoch, 3),
                    "element": element,
                    "alarm_type": alarm_type,
                    "rule_id": rule_id,
                    "vocab_terms": vocab_terms,
                    "text": text,  # hygiened + length-capped upstream; interpretation-only, never copied to output
                    "source": raw.get("source"),
                }
            )
        return out

    @staticmethod
    def _match_rule(alarm_type: str, text: str, rules: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
        """Deterministic bounded interpretation: map (alarm_type + free-text) to a policy rule vocabulary.

        Allow-list keyword match against each rule's ``alarm_types`` + ``vocabulary``. Returns the best rule
        (most vocab hits, alarm_type match is a strong signal) and the matched terms. No LLM.
        """
        low = (text or "").lower()
        best_rule: str | None = None
        best_terms: list[str] = []
        best_score = 0
        for rule_id, spec in rules.items():
            terms = [t for t in spec.get("vocabulary", []) if t and t in low]
            type_match = alarm_type in {str(t).strip().lower() for t in spec.get("alarm_types", [])}
            score = len(terms) + (2 if type_match else 0)
            if score > best_score:
                best_score, best_rule, best_terms = score, rule_id, terms
        return (best_rule, best_terms) if best_score > 0 else (None, [])

    # ── deterministic correlation grouping (Tool-split: topology + time-window + type co-occurrence) ──
    @staticmethod
    def group(
        signals: list[dict[str, Any]], topology: list[dict[str, Any]], policy: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Bundle alarms into candidate correlation groups by policy rule (deterministic, union-find).

        Two alarms are correlatable iff: same rule_id (both classified to a policy rule) AND within the
        time window AND (same element OR topology-adjacent elements). Connected components of size >= 2 are
        candidate groups (single-alarm clusters are not groups). This pure computation is the
        ``shared/tools/correlation`` reference split.
        """
        window = policy["time_window_sec"]
        adjacency = AlarmCorrelationService._adjacency(topology)
        n = len(signals)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)

        for i in range(n):
            for j in range(i + 1, n):
                a, b = signals[i], signals[j]
                if not a["rule_id"] or a["rule_id"] != b["rule_id"]:
                    continue
                if abs(a["ts_epoch"] - b["ts_epoch"]) > window:
                    continue
                if a["element"] == b["element"] or b["element"] in adjacency.get(a["element"], set()):
                    union(i, j)

        components: dict[int, list[int]] = {}
        for i in range(n):
            components.setdefault(find(i), []).append(i)

        groups: list[dict[str, Any]] = []
        gid = 0
        for _, members in sorted(components.items()):
            if len(members) < 2:
                continue  # a single-alarm cluster is not a correlation group
            gid += 1
            member_signals = [signals[m] for m in members]
            ts_values = [s["ts_epoch"] for s in member_signals]
            groups.append(
                {
                    "group_id": f"grp:{gid}",
                    "correlation_rule_id": member_signals[0]["rule_id"],
                    "correlation_rule_version": policy["version"],
                    "member_event_ids": [s["event_id"] for s in member_signals],
                    "member_elements": sorted({s["element"] for s in member_signals}),
                    "time_range": {"start": round(min(ts_values), 3), "end": round(max(ts_values), 3)},
                    "_members": member_signals,  # internal only; stripped before output
                }
            )
        return groups

    @staticmethod
    def _adjacency(topology: list[dict[str, Any]]) -> dict[str, set[str]]:
        """Build an undirected element-adjacency map from tokenized topology edges (no I/O)."""
        adj: dict[str, set[str]] = {}
        for edge in topology or []:
            if not isinstance(edge, dict):
                continue
            a = edge.get("element_a") or edge.get("a")
            b = edge.get("element_b") or edge.get("b")
            if a and b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        return adj

    # ── bounded interpretation + uncertainty labelling ────────────────────────
    @staticmethod
    def interpret(
        group: dict[str, Any],
        rules: dict[str, dict[str, Any]],
        maintenance_elements: set[str],
        topology_seen: set[str],
        masked_elements: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Interpret one candidate group: cite supporting events, attach an uncertainty label, compose a
        plain-language advisory rationale. Bounded, allow-list-only, deterministic — no LLM.

        Uncertainty:
          - ``needs_review`` on pre-interpretation containment (prompt-like/injection free-text), a partial
            vocab match (a member with no matched term), missing topology for the group's elements, a
            maintenance-window overlap ambiguity, or a member element whose identifier the platform masked
            (``masked_elements`` — the same-element / adjacency join behind the group is unverified).
          - ``high`` when every member has a vocab match, topology is present and no maintenance overlap.
          - ``medium`` otherwise.
        """
        members = group["_members"]
        rule_id = group["correlation_rule_id"]

        contained = any(contains_injection(m["text"]) for m in members)
        partial_match = any(not m["vocab_terms"] for m in members)
        maintenance_overlap = any(m["element"] in maintenance_elements for m in members)
        missing_topology = any(m["element"] not in topology_seen for m in members)
        element_masked = any(m["element"] in masked_elements for m in members)

        union_terms = set().union(*[set(m["vocab_terms"]) for m in members]) if members else set()
        if contained or partial_match or maintenance_overlap or missing_topology or element_masked:
            label = "needs_review"
        elif len(union_terms) >= 2:
            label = "high"
        else:
            label = "medium"

        # Citations: the resolved provenance of the group's members (S-1 produced `src:<sha8>` or None).
        # Deduplicated, order-stable. Missing on any member → group is uncited → S-3 fail-closed.
        citations: list[str] = []
        all_cited = True
        for m in members:
            src = m.get("source")
            if not src:
                all_cited = False
                continue
            if src not in citations:
                citations.append(src)

        reasons: list[str] = []
        if contained:
            reasons.append("a member alarm carried non-allow-listed / instruction-like text (contained)")
        if partial_match:
            reasons.append("a member's free-text did not match the rule vocabulary (partial)")
        if maintenance_overlap:
            reasons.append("a member element overlaps a maintenance window (ambiguous)")
        if missing_topology:
            reasons.append("topology data is incomplete for a member element")
        if element_masked:
            reasons.append(
                "a member element identifier was masked by the platform (ELEMENT_ID_MASKED), so the "
                "same-element / adjacency join behind this group is unverified"
            )
        rule_desc = rules.get(rule_id, {}).get("description", rule_id)
        rationale = (
            f"Candidate correlation under policy rule '{rule_id}' ({rule_desc}); "
            f"{len(members)} alarms grouped by topology adjacency + time-window proximity + "
            f"alarm-type co-occurrence."
        )
        if reasons:
            rationale += " Flagged needs-review: " + "; ".join(reasons) + "."

        result: dict[str, Any] = {
            "group_id": group["group_id"],
            "correlation_rule_id": rule_id,
            "correlation_rule_version": group["correlation_rule_version"],
            "supporting_event_ids": group["member_event_ids"],
            "member_elements": group["member_elements"],
            "time_range": group["time_range"],
            "alarm_count": len(members),
            "vocab_matches": sorted(union_terms),
            "uncertainty_label": label,
            "advisory_rationale": rationale,
            "citations": citations,
            "citation_complete": all_cited and bool(citations),
        }
        if element_masked:
            result["limitation"] = ELEMENT_ID_MASKED  # per-group code (only on groups it applies to)
        return result

    # ── evidence queue synthesis / summary ────────────────────────────────────
    @staticmethod
    def queue_summary(interpreted: list[dict[str, Any]], alarm_count: int) -> dict[str, Any]:
        """Queue-level rollup: alarm/group counts, uncertainty distribution, groups needing review."""
        distribution = {"high": 0, "medium": 0, "needs_review": 0}
        for g in interpreted:
            distribution[g["uncertainty_label"]] = distribution.get(g["uncertainty_label"], 0) + 1
        needs_review = [g["group_id"] for g in interpreted if g["uncertainty_label"] == "needs_review"]
        return {
            "alarm_count": alarm_count,
            "group_count": len(interpreted),
            "uncertainty_distribution": distribution,
            "groups_needing_review": needs_review,
        }
