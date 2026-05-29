"""Tests for speculative_branching.py — EVOI-gated parallel attempts.

Covers MULTI-AGENT-11 (advisory). Mirrors the V13 test layout in
AgentCorp: detector → gate → evaluator → dispatcher, plus persistence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from speculative_branching import (  # noqa: E402
    AttemptOutcome,
    BranchEvaluator,
    BranchResult,
    EVOIGate,
    SpeculativeDispatcher,
    UnitAttempt,
    UnitDescriptor,
    UnitHistoryStore,
    UnitUncertaintyDetector,
)


# ---------- UnitHistoryStore --------------------------------------------------


class TestUnitHistoryStore:
    def test_record_and_recall(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        store.record(_attempt("audit-log", 0, AttemptOutcome.FAILURE, 0.2))
        store.record(_attempt("audit-log", 1, AttemptOutcome.SUCCESS, 0.8))
        attempts = store.attempts("audit-log")
        assert [a.outcome for a in attempts] == [
            AttemptOutcome.FAILURE,
            AttemptOutcome.SUCCESS,
        ]

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "h.json"
        UnitHistoryStore(path).record(
            _attempt("x", 0, AttemptOutcome.SUCCESS, 0.9)
        )
        reopened = UnitHistoryStore(path)
        assert len(reopened.attempts("x")) == 1
        assert reopened.attempts("x")[0].quality_score == 0.9

    def test_success_rate_unknown_unit(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        assert store.success_rate("never-seen") is None

    def test_success_rate_mixed(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        store.record(_attempt("u", 0, AttemptOutcome.SUCCESS, 0.9))
        store.record(_attempt("u", 1, AttemptOutcome.FAILURE, 0.0))
        store.record(_attempt("u", 2, AttemptOutcome.SUCCESS, 0.7))
        assert store.success_rate("u") == pytest.approx(2 / 3)

    def test_corrupt_history_does_not_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "h.json"
        path.write_text("{not json", encoding="utf-8")
        # Should warn-and-continue, not raise.
        store = UnitHistoryStore(path)
        assert store.attempts("anything") == []


# ---------- UnitUncertaintyDetector ------------------------------------------


class TestUnitUncertaintyDetector:
    def test_unknown_unit_uses_prior(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        det = UnitUncertaintyDetector(store)
        sig = det.signal(_descriptor("new-unit"))
        # 0 history ⇒ prior 0.4 for the failure-rate component.
        assert 0.0 < sig.historical_failure_rate <= 0.5

    def test_high_failure_history_dominates(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        for i in range(4):
            store.record(_attempt("flaky", i, AttemptOutcome.FAILURE, 0.1))
        det = UnitUncertaintyDetector(store)
        sig = det.signal(_descriptor("flaky"))
        assert sig.historical_failure_rate > 0.85

    def test_recent_success_pulls_signal_down(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        for i in range(4):
            store.record(_attempt("u", i, AttemptOutcome.FAILURE, 0.1))
        baseline = UnitUncertaintyDetector(store).signal(_descriptor("u"))
        store.record(_attempt("u", 4, AttemptOutcome.SUCCESS, 0.9))
        improved = UnitUncertaintyDetector(store).signal(_descriptor("u"))
        assert improved.historical_failure_rate < baseline.historical_failure_rate

    def test_critical_path_lifts_total(self, tmp_path: Path) -> None:
        det = UnitUncertaintyDetector(UnitHistoryStore(tmp_path / "h.json"))
        peripheral = det.signal(_descriptor("u", on_critical_path=False))
        critical = det.signal(_descriptor("u", on_critical_path=True))
        assert critical.criticality > peripheral.criticality
        assert critical.total > peripheral.total

    def test_ambiguity_inverts_acceptance_count(self, tmp_path: Path) -> None:
        det = UnitUncertaintyDetector(UnitHistoryStore(tmp_path / "h.json"))
        well_specified = det.signal(
            _descriptor("u", acceptance_criteria_count=8)
        )
        vague = det.signal(_descriptor("u", acceptance_criteria_count=0))
        assert vague.ambiguity > well_specified.ambiguity

    def test_total_in_unit_interval(self, tmp_path: Path) -> None:
        det = UnitUncertaintyDetector(UnitHistoryStore(tmp_path / "h.json"))
        sig = det.signal(
            _descriptor(
                "u",
                depends_on=["a", "b", "c", "d", "e", "f"],
                owned_files=["a/**"] * 12,
                estimated_minutes=600,
                acceptance_criteria_count=0,
                on_critical_path=True,
            )
        )
        assert 0.0 <= sig.total <= 1.0

    def test_signals_sorted_by_total(self, tmp_path: Path) -> None:
        det = UnitUncertaintyDetector(UnitHistoryStore(tmp_path / "h.json"))
        units = [
            _descriptor("low", on_critical_path=False, acceptance_criteria_count=10),
            _descriptor("high", on_critical_path=True, acceptance_criteria_count=0),
            _descriptor("mid", on_critical_path=True, acceptance_criteria_count=4),
        ]
        result = det.signals(units)
        assert [s.unit_name for s in result] == ["high", "mid", "low"]

    def test_invalid_weights_rejected(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        with pytest.raises(ValueError, match="sum"):
            UnitUncertaintyDetector(store, weights={"complexity": 0.5})
        with pytest.raises(ValueError, match="non-negative"):
            UnitUncertaintyDetector(
                store,
                weights={
                    "historical_failure_rate": 0.5,
                    "complexity": 0.5,
                    "criticality": -0.0001,
                    "ambiguity": 0.0001,
                },
            )


# ---------- EVOIGate ---------------------------------------------------------


class TestEVOIGate:
    def test_below_floor_does_not_fork(self) -> None:
        gate = EVOIGate(min_uncertainty=0.5)
        signal = _signal(total=0.2)
        decision = gate.decide(signal, budget_remaining_usd=100)
        assert decision.fork is False
        assert "uncertainty" in decision.reason

    def test_no_budget_does_not_fork(self) -> None:
        gate = EVOIGate(n_branches=3, per_branch_cost_usd=2.0, min_uncertainty=0.2)
        signal = _signal(total=0.9, criticality=1.0)
        decision = gate.decide(signal, budget_remaining_usd=1.0)
        assert decision.fork is False
        assert "budget" in decision.reason

    def test_negative_evoi_does_not_fork(self) -> None:
        gate = EVOIGate(
            n_branches=4,
            per_branch_cost_usd=2.0,
            min_uncertainty=0.2,
            improvement_scale_usd=1.0,
        )
        signal = _signal(total=0.3, criticality=0.4)
        decision = gate.decide(signal, budget_remaining_usd=100)
        assert decision.fork is False
        assert "EVOI" in decision.reason

    def test_high_uncertainty_critical_unit_forks(self) -> None:
        # High-leverage unit: improvement scale lifted to $10 to reflect
        # a full sprint day saved if the speculative branch wins.
        gate = EVOIGate(
            n_branches=3,
            per_branch_cost_usd=1.5,
            min_uncertainty=0.35,
            improvement_scale_usd=10.0,
        )
        signal = _signal(total=0.8, criticality=1.0)
        decision = gate.decide(signal, budget_remaining_usd=100)
        assert decision.fork is True
        assert decision.evoi > 0
        assert decision.n_branches == 3

    def test_n_branches_minimum_enforced(self) -> None:
        with pytest.raises(ValueError, match="n_branches"):
            EVOIGate(n_branches=1)


# ---------- BranchEvaluator --------------------------------------------------


class TestBranchEvaluator:
    def test_picks_higher_quality_when_costs_equal(self) -> None:
        ev = BranchEvaluator(min_quality=0.5)
        results = [
            BranchResult("b0", "u", quality_score=0.6, cost_usd=1.0, duration_minutes=30),
            BranchResult("b1", "u", quality_score=0.9, cost_usd=1.0, duration_minutes=30),
        ]
        sel = ev.select(results)
        assert sel.has_winner
        assert sel.winner.branch_id == "b1"

    def test_drops_below_min_quality(self) -> None:
        ev = BranchEvaluator(min_quality=0.7)
        results = [
            BranchResult("b0", "u", quality_score=0.9, cost_usd=1.0, duration_minutes=30),
            BranchResult("b1", "u", quality_score=0.4, cost_usd=0.5, duration_minutes=10),
        ]
        sel = ev.select(results)
        assert sel.winner.branch_id == "b0"
        assert any(b.branch_id == "b1" for b, _ in sel.disqualified)

    def test_drops_over_cost_cap(self) -> None:
        ev = BranchEvaluator(min_quality=0.5, per_branch_cost_cap_usd=2.0)
        results = [
            BranchResult("b0", "u", quality_score=0.9, cost_usd=1.0, duration_minutes=30),
            BranchResult("b1", "u", quality_score=0.95, cost_usd=5.0, duration_minutes=20),
        ]
        sel = ev.select(results)
        assert sel.winner.branch_id == "b0"

    def test_drops_crashed(self) -> None:
        ev = BranchEvaluator(min_quality=0.5)
        results = [
            BranchResult("b0", "u", quality_score=0.9, cost_usd=1.0, duration_minutes=30),
            BranchResult(
                "b1", "u", quality_score=0.95, cost_usd=1.0, duration_minutes=10,
                completed=False, error="agent timed out"
            ),
        ]
        sel = ev.select(results)
        assert sel.winner.branch_id == "b0"

    def test_no_results_returns_no_winner(self) -> None:
        ev = BranchEvaluator()
        sel = ev.select([])
        assert sel.has_winner is False
        assert sel.rationale

    def test_pareto_keeps_non_dominated(self) -> None:
        ev = BranchEvaluator(min_quality=0.5)
        results = [
            BranchResult("hi-quality", "u", 0.95, cost_usd=3.0, duration_minutes=60),
            BranchResult("low-cost",   "u", 0.70, cost_usd=0.5, duration_minutes=10),
            BranchResult("dominated",  "u", 0.65, cost_usd=3.0, duration_minutes=70),
        ]
        sel = ev.select(results)
        front_ids = {b.branch_id for b in sel.pareto_front}
        assert "hi-quality" in front_ids
        assert "low-cost" in front_ids
        assert "dominated" not in front_ids

    def test_invalid_min_quality_rejected(self) -> None:
        with pytest.raises(ValueError):
            BranchEvaluator(min_quality=1.5)
        with pytest.raises(ValueError):
            BranchEvaluator(per_branch_cost_cap_usd=0)


# ---------- SpeculativeDispatcher --------------------------------------------


class TestSpeculativeDispatcher:
    def test_low_uncertainty_returns_single_branch(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        dispatcher = SpeculativeDispatcher(
            store, gate=EVOIGate(min_uncertainty=0.5)
        )
        unit = _descriptor(
            "trivial",
            depends_on=[],
            owned_files=["src/trivial.ts"],
            acceptance_criteria_count=10,
            on_critical_path=False,
        )
        plan = dispatcher.plan(unit, budget_remaining_usd=100)
        assert plan.fork is False
        assert len(plan.branches) == 1
        assert plan.branches[0].variant_label == "single"

    def test_high_uncertainty_returns_n_branches(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        for i in range(3):
            store.record(_attempt("flaky", i, AttemptOutcome.FAILURE, 0.0))
        dispatcher = SpeculativeDispatcher(
            store,
            gate=EVOIGate(
                n_branches=3,
                per_branch_cost_usd=1.0,
                min_uncertainty=0.3,
            ),
        )
        unit = _descriptor(
            "flaky",
            depends_on=["a", "b", "c"],
            owned_files=["src/a/**", "src/b/**", "src/c/**", "src/d/**"],
            acceptance_criteria_count=1,
            on_critical_path=True,
        )
        plan = dispatcher.plan(unit, budget_remaining_usd=100)
        assert plan.fork is True
        assert len(plan.branches) == 3
        assert {b.variant_label for b in plan.branches} == {
            "conservative",
            "balanced",
            "aggressive",
        }
        # Branch IDs and workspace dirs are unique.
        assert len({b.branch_id for b in plan.branches}) == 3
        assert len({b.workspace_subdir for b in plan.branches}) == 3
        assert plan.rule_id == "MULTI-AGENT-11"

    def test_record_outcomes_persists_all_branches(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        dispatcher = SpeculativeDispatcher(store)
        results = [
            BranchResult("u::b0", "u", 0.9, cost_usd=1.0, duration_minutes=30),
            BranchResult("u::b1", "u", 0.7, cost_usd=0.8, duration_minutes=25),
            BranchResult("u::b2", "u", 0.4, cost_usd=2.0, duration_minutes=40),
        ]
        sel = dispatcher.record_outcomes("u", results)
        assert sel.has_winner
        assert sel.winner.branch_id == "u::b0"
        # Every branch — winner AND losers — recorded.
        assert len(store.attempts("u")) == 3

    def test_record_outcomes_classifies_outcomes(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        dispatcher = SpeculativeDispatcher(store)
        results = [
            BranchResult("u::b0", "u", 0.9, cost_usd=1.0, duration_minutes=30),
            BranchResult("u::b1", "u", 0.3, cost_usd=0.5, duration_minutes=10),
            BranchResult(
                "u::b2", "u", 0.0, cost_usd=0.0, duration_minutes=99,
                completed=False, error="timed out"
            ),
            BranchResult(
                "u::b3", "u", 0.7, cost_usd=1.0, duration_minutes=20,
                error="contract mismatch"
            ),
        ]
        dispatcher.record_outcomes("u", results)
        outcomes = {a.branch_id: a.outcome for a in store.attempts("u")}
        assert outcomes["u::b0"] == AttemptOutcome.SUCCESS
        assert outcomes["u::b1"] == AttemptOutcome.QUALITY_REJECT
        assert outcomes["u::b2"] == AttemptOutcome.TIMEOUT
        assert outcomes["u::b3"] == AttemptOutcome.FAILURE

    def test_record_outcomes_rejects_empty_results(self, tmp_path: Path) -> None:
        store = UnitHistoryStore(tmp_path / "h.json")
        dispatcher = SpeculativeDispatcher(store)
        with pytest.raises(ValueError):
            dispatcher.record_outcomes("u", [])

    def test_dealscope_governance_modes_worked_example(self, tmp_path: Path) -> None:
        """Pins the worked example in docs/speculative-branching.md.

        The doc cites concrete numbers (component values, total signal,
        EVOI) for the dealscope DS-009 governance-modes unit. If anyone
        retunes the heuristics, this test fails and forces the doc to
        be updated alongside.
        """
        store = UnitHistoryStore(tmp_path / "h.json")
        dispatcher = SpeculativeDispatcher(
            store,
            gate=EVOIGate(
                n_branches=3,
                per_branch_cost_usd=1.5,
                min_uncertainty=0.35,
                improvement_scale_usd=10.0,
            ),
        )
        unit = UnitDescriptor(
            name="governance-modes",
            depends_on=["audit-log", "phase-orchestrator"],
            owned_files=["src/governance/**", "tests/test_governance.py"],
            estimated_minutes=180,
            acceptance_criteria_count=4,
            on_critical_path=True,
        )
        plan = dispatcher.plan(unit, budget_remaining_usd=20.0)
        sig = plan.signal
        assert sig.historical_failure_rate == pytest.approx(0.4, abs=1e-6)
        assert sig.complexity == pytest.approx(0.2829, abs=0.005)
        assert sig.criticality == pytest.approx(1.0, abs=1e-6)
        assert sig.ambiguity == pytest.approx(0.4, abs=1e-6)
        assert sig.total == pytest.approx(0.55, abs=0.01)
        assert plan.decision.evoi == pytest.approx(1.0, abs=0.05)
        assert plan.fork is True
        assert len(plan.branches) == 3

    def test_history_drives_next_plan(self, tmp_path: Path) -> None:
        """Round-trip: forked → recorded losers → next plan still forks."""
        store = UnitHistoryStore(tmp_path / "h.json")
        gate = EVOIGate(
            n_branches=3, per_branch_cost_usd=1.0, min_uncertainty=0.35
        )
        dispatcher = SpeculativeDispatcher(store, gate=gate)
        unit = _descriptor(
            "tricky",
            depends_on=["a", "b"],
            owned_files=["src/a/**", "src/b/**"],
            acceptance_criteria_count=1,
            on_critical_path=True,
        )

        first_plan = dispatcher.plan(unit, budget_remaining_usd=100)
        assert first_plan.fork is True

        # Two of three branches fail with low quality.
        results = [
            BranchResult("tricky::b0", "tricky", 0.2, 1.0, 30, error="API drift"),
            BranchResult("tricky::b1", "tricky", 0.3, 1.0, 35),
            BranchResult("tricky::b2", "tricky", 0.85, 1.2, 40),
        ]
        sel = dispatcher.record_outcomes("tricky", results)
        assert sel.winner.branch_id == "tricky::b2"

        # Next iteration: signal should still be high enough to fork
        # because losers contributed to the failure history.
        next_plan = dispatcher.plan(unit, budget_remaining_usd=100)
        assert next_plan.signal.contributing_attempts == 3


# ---------- helpers ----------------------------------------------------------


def _descriptor(
    name: str,
    *,
    depends_on: list[str] | None = None,
    owned_files: list[str] | None = None,
    estimated_minutes: int = 45,
    acceptance_criteria_count: int = 4,
    on_critical_path: bool = False,
) -> UnitDescriptor:
    return UnitDescriptor(
        name=name,
        depends_on=list(depends_on or []),
        owned_files=list(owned_files or [f"src/{name}/**"]),
        estimated_minutes=estimated_minutes,
        acceptance_criteria_count=acceptance_criteria_count,
        on_critical_path=on_critical_path,
    )


def _attempt(
    unit: str,
    idx: int,
    outcome: AttemptOutcome,
    quality: float,
) -> UnitAttempt:
    return UnitAttempt(
        unit_name=unit,
        attempt_index=idx,
        outcome=outcome,
        quality_score=quality,
        cost_usd=0.5,
        duration_minutes=10.0,
    )


def _signal(
    *,
    total: float,
    criticality: float = 0.6,
):
    from speculative_branching import UnitUncertaintySignal
    return UnitUncertaintySignal(
        unit_name="u",
        historical_failure_rate=0.5,
        complexity=0.5,
        criticality=criticality,
        ambiguity=0.5,
        total=total,
        contributing_attempts=0,
    )
