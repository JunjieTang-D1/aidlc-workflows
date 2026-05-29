"""
Speculative Branching for Multi-Agent Coordination
==================================================

Adds an EVOI-gated speculative-branching layer on top of the existing
CoordinationHarness so the orchestrator can dispatch N variant attempts
of high-uncertainty units in parallel and Pareto-pick the winner —
instead of one-shot-then-blind-retry.

Why this exists
---------------
The base harness (multi_agent_harness.py) is V12-equivalent: one agent
per unit, observe one trajectory, block on conflicts. That works when
every unit is well-specified. It does NOT work for units that

* historically fail (e.g. ``replay-runner``, ``governance-modes``),
* have ambiguous acceptance criteria that admit several reasonable
  rewrites (``audit-log`` could be JSONL or DynamoDB-backed),
* sit on the critical path so a retry bleeds the whole sprint's wall
  clock.

For those units we want to spend a bit more budget up-front to
explore alternatives. That is exactly what AgentCorp V13 does at the
TPG-node level. This module brings the same idea to AI-DLC unit
dispatch, with two differences:

1. **No LLM dependency.** Uncertainty + EVOI are deterministic
   functions of recorded history + plan metadata — same strategy V13
   used so iterations stay cheap and reproducible.
2. **Opt-in, advisory.** Forking is suggested via MULTI-AGENT-11
   (advisory) rather than enforced. The orchestrator is free to ignore
   the recommendation; cost-benefit stays the operator's call.

Components
----------
* :class:`UnitHistoryStore` — JSON-persisted attempt log keyed by
  ``unit_name``. Records (succeeded?, attempts, last_quality_score,
  last_failure_reasons[]). Persists across runs so the system actually
  learns over time.
* :class:`UnitUncertaintySignal` — per-unit profile mirroring V13's
  four components: historical_failure_rate, complexity, criticality,
  ambiguity. Each bounded to ``[0, 1]``; ``total`` is a weighted
  combination tuned so a unit with a poor track record on a critical
  path crosses the EVOI fork floor.
* :class:`EVOIGate` — gates on ``EVOI = P(better) · improvement$ −
  branch_cost``. Conservative: any of (signal magnitude, budget
  headroom, EVOI sign) failing means *don't fork*.
* :class:`BranchEvaluator` — Pareto front on (quality, cost, latency)
  with min-quality and budget-cap screening. Returns winner + losers
  + diagnostics, mirroring V13's BranchSelection.
* :class:`SpeculativeDispatcher` — wraps :class:`CoordinationHarness`,
  consults the gate before each unit dispatch, returns a
  :class:`DispatchPlan` (single agent OR N-branch fan-out).

Extension hook: MULTI-AGENT-11 (advisory). See
``extensions/agentic/multi-agent-coordination/multi-agent-coordination.md``
for the rule body and verification criteria.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# History persistence
# ----------------------------------------------------------------------


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    QUALITY_REJECT = "quality_reject"


@dataclass
class UnitAttempt:
    """One past attempt at a unit. Persisted to disk."""

    unit_name: str
    attempt_index: int
    outcome: AttemptOutcome
    quality_score: float  # [0, 1] — higher is better; 0 if unknown
    cost_usd: float
    duration_minutes: float
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    failure_reasons: list[str] = field(default_factory=list)
    branch_id: str | None = None  # populated when this attempt was a fork branch

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_name": self.unit_name,
            "attempt_index": self.attempt_index,
            "outcome": self.outcome.value,
            "quality_score": self.quality_score,
            "cost_usd": self.cost_usd,
            "duration_minutes": self.duration_minutes,
            "timestamp": self.timestamp,
            "failure_reasons": list(self.failure_reasons),
            "branch_id": self.branch_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnitAttempt":
        return cls(
            unit_name=data["unit_name"],
            attempt_index=int(data["attempt_index"]),
            outcome=AttemptOutcome(data["outcome"]),
            quality_score=float(data.get("quality_score", 0.0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            duration_minutes=float(data.get("duration_minutes", 0.0)),
            timestamp=data.get("timestamp") or datetime.now(UTC).isoformat(),
            failure_reasons=list(data.get("failure_reasons") or []),
            branch_id=data.get("branch_id"),
        )


class UnitHistoryStore:
    """JSON-persisted attempt log keyed by ``unit_name``.

    Thread-safe for in-process concurrent appends (the harness can be
    shared across worker threads dispatching different units). It is
    NOT safe for cross-process writes — the orchestrator is the single
    writer by convention (mirrors MULTI-AGENT-06).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._attempts: dict[str, list[UnitAttempt]] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("history store at %s unreadable: %s", self.path, exc)
            return
        for unit, raw_attempts in data.items():
            self._attempts[unit] = [UnitAttempt.from_dict(a) for a in raw_attempts]

    def record(self, attempt: UnitAttempt) -> None:
        with self._lock:
            self._attempts.setdefault(attempt.unit_name, []).append(attempt)
            self._flush_unlocked()

    def attempts(self, unit_name: str) -> list[UnitAttempt]:
        with self._lock:
            return list(self._attempts.get(unit_name, []))

    def success_rate(self, unit_name: str) -> float | None:
        """Return success rate over recorded attempts, or ``None`` if no
        history exists. ``None`` is a deliberate signal — the gate
        treats unknown units differently from confirmed-flaky ones.
        """
        with self._lock:
            attempts = self._attempts.get(unit_name, [])
        if not attempts:
            return None
        ok = sum(1 for a in attempts if a.outcome == AttemptOutcome.SUCCESS)
        return ok / len(attempts)

    def best_quality(self, unit_name: str) -> float | None:
        with self._lock:
            attempts = self._attempts.get(unit_name, [])
        if not attempts:
            return None
        return max(a.quality_score for a in attempts)

    def all_units(self) -> list[str]:
        with self._lock:
            return list(self._attempts.keys())

    def _flush_unlocked(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                unit: [a.to_dict() for a in attempts]
                for unit, attempts in self._attempts.items()
            }
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("history flush failed at %s: %s", self.path, exc)


# ----------------------------------------------------------------------
# Uncertainty signal
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class UnitUncertaintySignal:
    """Per-unit uncertainty profile, all components in ``[0, 1]``."""

    unit_name: str
    historical_failure_rate: float  # 1 - success_rate, or prior for unknowns
    complexity: float  # plan-derived: deps + owned-files breadth
    criticality: float  # 1.0 if on the longest path through the DAG
    ambiguity: float  # 1.0 when acceptance criteria are sparse / vague
    total: float
    contributing_attempts: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_name": self.unit_name,
            "historical_failure_rate": self.historical_failure_rate,
            "complexity": self.complexity,
            "criticality": self.criticality,
            "ambiguity": self.ambiguity,
            "total": self.total,
            "contributing_attempts": self.contributing_attempts,
        }


# Mirrors V13 mixing-weights philosophy: variance/disagreement carries
# the most weight, economic-impact (here: criticality) is the next
# tier, then prior-failure history, with ambiguity as a tie-breaker.
DEFAULT_UNCERTAINTY_WEIGHTS = {
    "historical_failure_rate": 0.30,
    "complexity": 0.25,
    "criticality": 0.30,
    "ambiguity": 0.15,
}


@dataclass
class UnitDescriptor:
    """Static, plan-derived facts about a unit. Not the runtime Unit.

    Kept separate so callers can construct signals without needing a
    fully-instantiated harness — useful for what-if analysis and the
    dry-run planner CLI.
    """

    name: str
    depends_on: list[str] = field(default_factory=list)
    owned_files: list[str] = field(default_factory=list)
    estimated_minutes: int = 45
    acceptance_criteria_count: int = 0  # 0 ⇒ maximally ambiguous
    on_critical_path: bool = False


class UnitUncertaintyDetector:
    """Map ``UnitDescriptor`` + history → :class:`UnitUncertaintySignal`.

    The detector is pure-Python; no LLM call. Each component is bounded
    to ``[0, 1]`` and combined with :data:`DEFAULT_UNCERTAINTY_WEIGHTS`.
    The ``total`` field is what :class:`EVOIGate` consumes.
    """

    # Heuristic anchors. Tuned so a unit with no history but 6 deps,
    # broad ownership, on the critical path, and 1 acceptance bullet
    # crosses the default 0.35 fork floor.
    _COMPLEXITY_DEP_FLOOR = 1
    _COMPLEXITY_DEP_CEILING = 6
    _COMPLEXITY_FILES_FLOOR = 1
    _COMPLEXITY_FILES_CEILING = 8
    _AMBIGUITY_AC_FLOOR = 1
    _AMBIGUITY_AC_CEILING = 6
    _UNKNOWN_HISTORY_PRIOR = 0.4  # mid-band, leans cautious

    def __init__(
        self,
        history: UnitHistoryStore,
        *,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.history = history
        self.weights = dict(weights or DEFAULT_UNCERTAINTY_WEIGHTS)
        _validate_weights(self.weights)

    def signal(self, unit: UnitDescriptor) -> UnitUncertaintySignal:
        attempts = self.history.attempts(unit.name)
        hf = self._historical_failure_rate(unit.name, attempts)
        cx = self._complexity(unit)
        cr = 1.0 if unit.on_critical_path else 0.4
        am = self._ambiguity(unit)

        total = (
            self.weights["historical_failure_rate"] * hf
            + self.weights["complexity"] * cx
            + self.weights["criticality"] * cr
            + self.weights["ambiguity"] * am
        )
        return UnitUncertaintySignal(
            unit_name=unit.name,
            historical_failure_rate=hf,
            complexity=cx,
            criticality=cr,
            ambiguity=am,
            total=_clip01(total),
            contributing_attempts=len(attempts),
        )

    def signals(
        self, units: list[UnitDescriptor]
    ) -> list[UnitUncertaintySignal]:
        out = [self.signal(u) for u in units]
        out.sort(key=lambda s: s.total, reverse=True)
        return out

    # -- components ----------------------------------------------------

    def _historical_failure_rate(
        self, unit_name: str, attempts: list[UnitAttempt]
    ) -> float:
        if not attempts:
            return self._UNKNOWN_HISTORY_PRIOR
        # Base = empirical failure rate over all recorded attempts;
        # the most-recent outcome adjusts it by ±0.15 so a successful
        # retry after a string of failures (or vice versa) is reflected
        # immediately rather than waiting for the long-run average to
        # shift. Deliberately simple — a richer time-decay scheme would
        # need more data than we typically have per unit.
        sr = self.history.success_rate(unit_name) or 0.0
        last = attempts[-1]
        recent_bonus = 0.15 if last.outcome == AttemptOutcome.SUCCESS else -0.15
        return _clip01(1.0 - (sr + recent_bonus))

    @classmethod
    def _complexity(cls, unit: UnitDescriptor) -> float:
        deps_score = _ramp(
            len(unit.depends_on),
            cls._COMPLEXITY_DEP_FLOOR,
            cls._COMPLEXITY_DEP_CEILING,
        )
        files_score = _ramp(
            len(unit.owned_files),
            cls._COMPLEXITY_FILES_FLOOR,
            cls._COMPLEXITY_FILES_CEILING,
        )
        # Long estimates flag complex units even if file/dep counts are
        # small — e.g. an ML training story can own 1 file and take a
        # day to converge.
        time_score = _ramp(unit.estimated_minutes, 30, 240)
        return _clip01(0.4 * deps_score + 0.4 * files_score + 0.2 * time_score)

    @classmethod
    def _ambiguity(cls, unit: UnitDescriptor) -> float:
        # Sparse acceptance criteria ⇒ many valid rewrites ⇒ more
        # value in exploring branches. We invert the AC count so 0
        # criteria scores 1.0 (maximally ambiguous) and 6+ criteria
        # scores 0.0 (well-specified).
        if unit.acceptance_criteria_count <= cls._AMBIGUITY_AC_FLOOR - 1:
            return 1.0
        if unit.acceptance_criteria_count >= cls._AMBIGUITY_AC_CEILING:
            return 0.0
        span = cls._AMBIGUITY_AC_CEILING - cls._AMBIGUITY_AC_FLOOR
        offset = unit.acceptance_criteria_count - cls._AMBIGUITY_AC_FLOOR
        return _clip01(1.0 - (offset / span))


# ----------------------------------------------------------------------
# EVOI gate
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class EVOIDecision:
    fork: bool
    unit_name: str | None
    n_branches: int
    evoi: float
    expected_improvement_usd: float
    branch_cost_usd: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fork": self.fork,
            "unit_name": self.unit_name,
            "n_branches": self.n_branches,
            "evoi": self.evoi,
            "expected_improvement_usd": self.expected_improvement_usd,
            "branch_cost_usd": self.branch_cost_usd,
            "reason": self.reason,
        }


class EVOIGate:
    """Decide whether to fork given a signal + budget headroom.

    Formula (mirrors V13_DESIGN.md):

        EVOI = P(better_outcome) · |improvement| − branch_cost_total

    Conservative: any of (uncertainty < floor, budget headroom < cost,
    EVOI ≤ 0) returns ``fork=False`` with the reason filled in.
    """

    def __init__(
        self,
        *,
        n_branches: int = 3,
        per_branch_cost_usd: float = 1.5,
        min_uncertainty: float = 0.35,
        improvement_scale_usd: float = 5.0,
    ) -> None:
        if n_branches < 2:
            raise ValueError("n_branches must be >= 2 for branching to be useful")
        self.n_branches = n_branches
        self.per_branch_cost_usd = per_branch_cost_usd
        self.min_uncertainty = min_uncertainty
        self.improvement_scale_usd = improvement_scale_usd

    def decide(
        self,
        signal: UnitUncertaintySignal,
        *,
        budget_remaining_usd: float,
    ) -> EVOIDecision:
        # Treat criticality as the canonical "value if better" lever:
        # forking on a critical-path unit is worth more than the same
        # signal magnitude on a peripheral one.
        expected_improvement = signal.criticality * self.improvement_scale_usd
        branch_cost_total = self.per_branch_cost_usd * self.n_branches
        evoi = signal.total * expected_improvement - branch_cost_total

        if signal.total < self.min_uncertainty:
            return EVOIDecision(
                fork=False,
                unit_name=signal.unit_name,
                n_branches=self.n_branches,
                evoi=evoi,
                expected_improvement_usd=expected_improvement,
                branch_cost_usd=branch_cost_total,
                reason=(
                    f"uncertainty {signal.total:.2f} < floor "
                    f"{self.min_uncertainty:.2f}"
                ),
            )
        if budget_remaining_usd < branch_cost_total:
            return EVOIDecision(
                fork=False,
                unit_name=signal.unit_name,
                n_branches=self.n_branches,
                evoi=evoi,
                expected_improvement_usd=expected_improvement,
                branch_cost_usd=branch_cost_total,
                reason=(
                    f"budget ${budget_remaining_usd:.2f} < required "
                    f"${branch_cost_total:.2f}"
                ),
            )
        if evoi <= 0:
            return EVOIDecision(
                fork=False,
                unit_name=signal.unit_name,
                n_branches=self.n_branches,
                evoi=evoi,
                expected_improvement_usd=expected_improvement,
                branch_cost_usd=branch_cost_total,
                reason=f"EVOI ${evoi:.2f} <= 0",
            )
        return EVOIDecision(
            fork=True,
            unit_name=signal.unit_name,
            n_branches=self.n_branches,
            evoi=evoi,
            expected_improvement_usd=expected_improvement,
            branch_cost_usd=branch_cost_total,
            reason=(
                f"EVOI=${evoi:.2f} (uncertainty={signal.total:.2f}, "
                f"impact=${expected_improvement:.2f}, "
                f"cost=${branch_cost_total:.2f})"
            ),
        )


# ----------------------------------------------------------------------
# Branch results & Pareto selection
# ----------------------------------------------------------------------


@dataclass
class BranchResult:
    """One branch's outcome — the input to Pareto selection."""

    branch_id: str
    unit_name: str
    quality_score: float  # [0, 1]
    cost_usd: float
    duration_minutes: float
    completed: bool = True
    error: str | None = None

    @property
    def crashed(self) -> bool:
        return not self.completed or self.error is not None


@dataclass
class BranchSelection:
    winner: BranchResult | None
    losers: list[BranchResult]
    disqualified: list[tuple[BranchResult, str]]
    pareto_front: list[BranchResult]
    rationale: str

    @property
    def has_winner(self) -> bool:
        return self.winner is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner.branch_id if self.winner else None,
            "losers": [b.branch_id for b in self.losers],
            "disqualified": [
                {"branch_id": b.branch_id, "reason": r}
                for b, r in self.disqualified
            ],
            "pareto_front": [b.branch_id for b in self.pareto_front],
            "rationale": self.rationale,
        }


class BranchEvaluator:
    """Pareto-front selection on (quality, cost, latency) with floors.

    Mirrors V13's BranchEvaluator: drop crashed and below-floor
    branches first, build the Pareto front on the remainder, then
    tie-break by composite score:

        composite = w_q · quality − w_c · cost_norm − w_l · latency_norm

    Costs and latencies are normalized within the branch set (divided
    by the per-set max) so the composite is comparable across runs
    regardless of the absolute USD / minute scale. With default weights
    ``(0.6, 0.25, 0.15)`` it falls in roughly ``[-0.4, 0.6]``.
    """

    def __init__(
        self,
        *,
        min_quality: float = 0.5,
        per_branch_cost_cap_usd: float = 5.0,
        composite_weights: dict[str, float] | None = None,
        drop_crashed: bool = True,
    ) -> None:
        if not 0.0 <= min_quality <= 1.0:
            raise ValueError("min_quality must be in [0, 1]")
        if per_branch_cost_cap_usd <= 0:
            raise ValueError("per_branch_cost_cap_usd must be > 0")
        self.min_quality = min_quality
        self.per_branch_cost_cap_usd = per_branch_cost_cap_usd
        self.composite_weights = dict(
            composite_weights
            or {"quality": 0.6, "cost": 0.25, "latency": 0.15}
        )
        _validate_weights(self.composite_weights)
        self.drop_crashed = drop_crashed

    def select(self, results: list[BranchResult]) -> BranchSelection:
        if not results:
            return BranchSelection(
                winner=None,
                losers=[],
                disqualified=[],
                pareto_front=[],
                rationale="no branches to select from",
            )

        survivors: list[BranchResult] = []
        disqualified: list[tuple[BranchResult, str]] = []

        for r in results:
            if self.drop_crashed and r.crashed:
                disqualified.append((r, f"crashed: {r.error or 'incomplete'}"))
                continue
            if r.quality_score < self.min_quality:
                disqualified.append(
                    (r, f"quality {r.quality_score:.2f} < floor {self.min_quality:.2f}")
                )
                continue
            if r.cost_usd > self.per_branch_cost_cap_usd:
                disqualified.append(
                    (
                        r,
                        f"cost ${r.cost_usd:.2f} > cap "
                        f"${self.per_branch_cost_cap_usd:.2f}",
                    )
                )
                continue
            survivors.append(r)

        if not survivors:
            return BranchSelection(
                winner=None,
                losers=[],
                disqualified=disqualified,
                pareto_front=[],
                rationale="all branches disqualified",
            )

        front = _pareto_front(survivors)
        winner = self._tiebreak(front, survivors)
        losers = [b for b in survivors if b is not winner]
        return BranchSelection(
            winner=winner,
            losers=losers,
            disqualified=disqualified,
            pareto_front=front,
            rationale=(
                f"Pareto front size {len(front)}; tie-break by composite over "
                f"{len(survivors)} survivors ({len(disqualified)} dropped)"
            ),
        )

    def _tiebreak(
        self, front: list[BranchResult], survivors: list[BranchResult]
    ) -> BranchResult:
        max_cost = max((b.cost_usd for b in survivors), default=1.0) or 1.0
        max_latency = (
            max((b.duration_minutes for b in survivors), default=1.0) or 1.0
        )
        w = self.composite_weights

        def composite(b: BranchResult) -> float:
            return (
                w["quality"] * b.quality_score
                - w["cost"] * (b.cost_usd / max_cost)
                - w["latency"] * (b.duration_minutes / max_latency)
            )

        return max(front, key=composite)


def _pareto_front(branches: list[BranchResult]) -> list[BranchResult]:
    """Branches not dominated by any other on (quality↑, cost↓, latency↓)."""
    front: list[BranchResult] = []
    for b in branches:
        dominated = False
        for other in branches:
            if other is b:
                continue
            if (
                other.quality_score >= b.quality_score
                and other.cost_usd <= b.cost_usd
                and other.duration_minutes <= b.duration_minutes
                and (
                    other.quality_score > b.quality_score
                    or other.cost_usd < b.cost_usd
                    or other.duration_minutes < b.duration_minutes
                )
            ):
                dominated = True
                break
        if not dominated:
            front.append(b)
    return front


# ----------------------------------------------------------------------
# Speculative dispatcher
# ----------------------------------------------------------------------


@dataclass
class DispatchPlan:
    """Output of :meth:`SpeculativeDispatcher.plan`.

    ``branches`` has one entry when the gate decides not to fork
    (single-agent dispatch), N entries when it decides to fork.
    ``rule_id`` is always ``"MULTI-AGENT-11"`` so callers can attribute
    the recommendation to the advisory rule.
    """

    unit_name: str
    fork: bool
    branches: list["BranchPlan"]
    decision: EVOIDecision
    signal: UnitUncertaintySignal
    rule_id: str = "MULTI-AGENT-11"

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit_name": self.unit_name,
            "fork": self.fork,
            "rule_id": self.rule_id,
            "branches": [b.as_dict() for b in self.branches],
            "decision": self.decision.as_dict(),
            "signal": self.signal.as_dict(),
        }


@dataclass
class BranchPlan:
    """One agent assignment within a :class:`DispatchPlan`."""

    branch_id: str
    unit_name: str
    variant_label: str  # e.g. "conservative" | "balanced" | "aggressive"
    suggested_branch_name: str  # git branch name suggestion
    workspace_subdir: str  # workspace directory suggestion
    instruction_preamble: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "unit_name": self.unit_name,
            "variant_label": self.variant_label,
            "suggested_branch_name": self.suggested_branch_name,
            "workspace_subdir": self.workspace_subdir,
            "instruction_preamble": self.instruction_preamble,
        }


_VARIANT_PREAMBLES = {
    "conservative": (
        "Lean conservative: smallest viable change. Reuse existing patterns "
        "and avoid new dependencies."
    ),
    "balanced": (
        "Lean balanced: median sensible rewrite. Standard-library-first; "
        "introduce one new dependency only if it materially simplifies."
    ),
    "aggressive": (
        "Lean aggressive: boldest acceptable rewrite. You may restructure "
        "the unit's owned files if it improves clarity or testability."
    ),
}
_DEFAULT_VARIANT_LABELS = ("conservative", "balanced", "aggressive")


class SpeculativeDispatcher:
    """Wraps a :class:`CoordinationHarness`-style ownership/dependency
    view with EVOI-gated speculative branching.

    The dispatcher does NOT run agents itself — it returns a
    :class:`DispatchPlan` that the orchestrator (human or coordinating
    agent) can execute via whatever runtime is in use (Strands,
    LangGraph, GitHub Actions, IDE composer, etc.).

    After branches finish, callers feed every :class:`BranchResult`
    into :meth:`record_outcomes`. The dispatcher Pareto-selects the
    winner, persists every branch's outcome to the
    :class:`UnitHistoryStore` (so future signals know about both the
    winner *and* the losers — that's the contrastive-learning hook),
    and returns the :class:`BranchSelection`.
    """

    def __init__(
        self,
        history: UnitHistoryStore,
        gate: EVOIGate | None = None,
        *,
        detector: UnitUncertaintyDetector | None = None,
        evaluator: BranchEvaluator | None = None,
        variant_labels: tuple[str, ...] = _DEFAULT_VARIANT_LABELS,
    ) -> None:
        self.history = history
        self.detector = detector or UnitUncertaintyDetector(history)
        self.gate = gate or EVOIGate()
        self.evaluator = evaluator or BranchEvaluator()
        if len(variant_labels) < 2:
            raise ValueError("need at least 2 variant labels for branching")
        self.variant_labels = variant_labels

    def plan(
        self,
        unit: UnitDescriptor,
        *,
        budget_remaining_usd: float,
    ) -> DispatchPlan:
        signal = self.detector.signal(unit)
        decision = self.gate.decide(
            signal, budget_remaining_usd=budget_remaining_usd
        )

        if not decision.fork:
            return DispatchPlan(
                unit_name=unit.name,
                fork=False,
                branches=[self._single_branch_plan(unit)],
                decision=decision,
                signal=signal,
            )

        branches = self._fan_out(unit, decision.n_branches)
        return DispatchPlan(
            unit_name=unit.name,
            fork=True,
            branches=branches,
            decision=decision,
            signal=signal,
        )

    def record_outcomes(
        self,
        unit_name: str,
        results: list[BranchResult],
    ) -> BranchSelection:
        """Persist every branch's outcome and Pareto-select the winner.

        Every result is recorded — winner AND losers — so the next
        :class:`UnitUncertaintyDetector` call sees the full sample.
        That is the simplest form of contrastive learning available
        without running gradient extraction.
        """
        if not results:
            raise ValueError("results must contain at least one BranchResult")

        attempt_index = len(self.history.attempts(unit_name))
        for r in results:
            outcome = self._classify_outcome(r)
            self.history.record(
                UnitAttempt(
                    unit_name=unit_name,
                    attempt_index=attempt_index,
                    outcome=outcome,
                    quality_score=r.quality_score,
                    cost_usd=r.cost_usd,
                    duration_minutes=r.duration_minutes,
                    failure_reasons=[r.error] if r.error else [],
                    branch_id=r.branch_id,
                )
            )
            attempt_index += 1

        return self.evaluator.select(results)

    # -- helpers -------------------------------------------------------

    def _fan_out(
        self, unit: UnitDescriptor, n_branches: int
    ) -> list[BranchPlan]:
        labels = self.variant_labels
        if n_branches > len(labels):
            # Repeat the last label rather than refusing — keeps the
            # API predictable when callers ask for a non-standard N.
            labels = labels + (labels[-1],) * (n_branches - len(labels))
        return [
            BranchPlan(
                branch_id=f"{unit.name}::b{i}",
                unit_name=unit.name,
                variant_label=labels[i],
                suggested_branch_name=f"unit/{unit.name}/b{i}-{labels[i]}",
                workspace_subdir=f".worktrees/{unit.name}/b{i}-{labels[i]}",
                instruction_preamble=_VARIANT_PREAMBLES.get(
                    labels[i], _VARIANT_PREAMBLES["balanced"]
                ),
            )
            for i in range(n_branches)
        ]

    def _single_branch_plan(self, unit: UnitDescriptor) -> BranchPlan:
        return BranchPlan(
            branch_id=f"{unit.name}::single",
            unit_name=unit.name,
            variant_label="single",
            suggested_branch_name=f"unit/{unit.name}",
            workspace_subdir=f".worktrees/{unit.name}",
            instruction_preamble=(
                "Standard single-agent dispatch — no speculative branching "
                "needed for this unit."
            ),
        )

    @staticmethod
    def _classify_outcome(r: BranchResult) -> AttemptOutcome:
        if not r.completed:
            return AttemptOutcome.TIMEOUT
        if r.error:
            return AttemptOutcome.FAILURE
        if r.quality_score < 0.5:
            return AttemptOutcome.QUALITY_REJECT
        return AttemptOutcome.SUCCESS


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _clip01(x: float) -> float:
    if math.isnan(x):
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _ramp(value: float, floor: float, ceiling: float) -> float:
    """Linear ramp clipped to ``[0, 1]``. ``ceiling - floor`` must be > 0."""
    if ceiling <= floor:
        raise ValueError("ramp ceiling must be greater than floor")
    return _clip01((value - floor) / (ceiling - floor))


def _validate_weights(weights: dict[str, float]) -> None:
    """Reject weight maps that would silently distort signals."""
    if not weights:
        raise ValueError("weights map must be non-empty")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights must be non-negative")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        # Don't auto-normalize — silent renormalization makes the
        # callers' tuning untraceable. Reject loudly instead.
        raise ValueError(
            f"weights must sum to 1.0 (got {total:.4f}); fix or normalize"
        )
