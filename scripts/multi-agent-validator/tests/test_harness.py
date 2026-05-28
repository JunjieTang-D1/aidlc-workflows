"""Tests for the multi-agent coordination harness."""

import json
import time
from pathlib import Path

import pytest

# Add parent to path for import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from multi_agent_harness import (
    CoordinationHarness,
    DependencyGraph,
    FileOwnershipRegistry,
    StragglerMonitor,
    Unit,
    UnitStatus,
    Violation,
    ViolationType,
)


class TestFileOwnershipRegistry:
    """Tests for MULTI-AGENT-01 enforcement."""

    def test_register_ownership(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**", "tests/auth/**"])
        assert registry.get_owner("src/auth/login.ts") == "auth-service"

    def test_write_allowed_for_owner(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**"])
        assert registry.check_write_allowed("agent-1", "src/auth/login.ts", "auth-service") is True

    def test_write_blocked_for_non_owner(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**"])
        assert registry.check_write_allowed("agent-2", "src/auth/login.ts", "catalog-service") is False

    def test_duplicate_ownership_raises(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**"])
        with pytest.raises(ValueError, match="already owned"):
            registry.register("catalog-service", ["src/auth/**"])

    def test_unowned_file_allowed(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**"])
        assert registry.check_write_allowed("agent-1", "src/other/file.ts", "auth-service") is True

    def test_directory_glob_pattern(self):
        registry = FileOwnershipRegistry()
        registry.register("auth-service", ["src/auth/**"])
        assert registry.get_owner("src/auth/middleware/jwt.ts") == "auth-service"
        assert registry.get_owner("src/catalog/product.ts") is None


class TestDependencyGraph:
    """Tests for MULTI-AGENT-02 enforcement."""

    def test_valid_dag(self):
        graph = DependencyGraph()
        graph.add_unit("auth", [])
        graph.add_unit("catalog", [])
        graph.add_unit("gateway", ["auth", "catalog"])
        is_valid, cycle = graph.validate_dag()
        assert is_valid is True
        assert cycle == []

    def test_cycle_detection(self):
        graph = DependencyGraph()
        graph.add_unit("a", ["c"])
        graph.add_unit("b", ["a"])
        graph.add_unit("c", ["b"])
        is_valid, cycle = graph.validate_dag()
        assert is_valid is False
        assert len(cycle) > 0

    def test_independent_units(self):
        graph = DependencyGraph()
        graph.add_unit("auth", [])
        graph.add_unit("catalog", [])
        graph.add_unit("notifications", [])
        graph.add_unit("gateway", ["auth", "catalog", "notifications"])
        independent = graph.get_independent_units()
        assert set(independent) == {"auth", "catalog", "notifications"}
        assert "gateway" not in independent

    def test_can_start_with_unmet_deps(self):
        graph = DependencyGraph()
        graph.add_unit("auth", [])
        graph.add_unit("gateway", ["auth"])
        can_start, unsatisfied = graph.can_start("gateway")
        assert can_start is False
        assert "auth" in unsatisfied

    def test_can_start_after_dep_completed(self):
        graph = DependencyGraph()
        graph.add_unit("auth", [])
        graph.add_unit("gateway", ["auth"])
        graph.mark_completed("auth")
        can_start, unsatisfied = graph.can_start("gateway")
        assert can_start is True
        assert unsatisfied == []

    def test_parallelizable_fraction(self):
        graph = DependencyGraph()
        graph.add_unit("auth", [])
        graph.add_unit("catalog", [])
        graph.add_unit("notifications", [])
        graph.add_unit("gateway", ["auth", "catalog", "notifications"])
        assert graph.parallelizable_fraction() == 0.75

    def test_amdahl_speedup(self):
        graph = DependencyGraph()
        graph.add_unit("a", [])
        graph.add_unit("b", [])
        graph.add_unit("c", [])
        graph.add_unit("d", ["a", "b", "c"])
        # p = 0.75, N = 3: speedup = 1 / (0.25 + 0.75/3) = 1 / 0.5 = 2.0
        assert graph.amdahl_speedup(3) == pytest.approx(2.0, rel=0.01)

    def test_fully_sequential(self):
        graph = DependencyGraph()
        graph.add_unit("a", [])
        graph.add_unit("b", ["a"])
        graph.add_unit("c", ["b"])
        # Only "a" is independent, p = 1/3 ≈ 0.33
        assert graph.parallelizable_fraction() == pytest.approx(0.333, rel=0.01)
        # With 10 agents: speedup = 1 / (0.67 + 0.33/10) = 1 / 0.703 ≈ 1.42
        assert graph.amdahl_speedup(10) == pytest.approx(1.42, rel=0.05)


class TestCoordinationHarness:
    """Integration tests for the full harness."""

    @pytest.fixture
    def harness(self):
        units = [
            Unit(name="auth-service", owned_files=["src/auth/**"], depends_on=[], produces=["JWT middleware"]),
            Unit(name="catalog-service", owned_files=["src/catalog/**"], depends_on=[], produces=["Product API"]),
            Unit(name="notification-service", owned_files=["src/notifications/**"], depends_on=[], produces=["Events"]),
            Unit(name="api-gateway", owned_files=["src/gateway/**"], depends_on=["auth-service", "catalog-service", "notification-service"], produces=["Gateway"]),
        ]
        return CoordinationHarness(units)

    def test_assign_independent_unit(self, harness):
        result = harness.assign_agent("agent-1", "auth-service", "feat/auth", "/workspace/auth")
        assert not isinstance(result, Violation)
        assert result.agent_id == "agent-1"
        assert harness.units["auth-service"].status == UnitStatus.IN_PROGRESS

    def test_assign_dependent_unit_blocked(self, harness):
        result = harness.assign_agent("agent-4", "api-gateway", "feat/gw", "/workspace/gw")
        assert isinstance(result, Violation)
        assert result.violation_type == ViolationType.DEPENDENCY_RACE
        assert result.rule_id == "MULTI-AGENT-02"

    def test_assign_after_deps_completed(self, harness):
        # Complete all dependencies
        harness.assign_agent("agent-1", "auth-service", "feat/auth", "/ws/1")
        harness.signal_completion("agent-1")
        harness.assign_agent("agent-2", "catalog-service", "feat/cat", "/ws/2")
        harness.signal_completion("agent-2")
        harness.assign_agent("agent-3", "notification-service", "feat/notif", "/ws/3")
        harness.signal_completion("agent-3")

        # Now api-gateway should be assignable
        result = harness.assign_agent("agent-4", "api-gateway", "feat/gw", "/ws/4")
        assert not isinstance(result, Violation)

    def test_file_write_enforcement(self, harness):
        harness.assign_agent("agent-1", "auth-service", "feat/auth", "/ws/1")

        # Agent can write to own files
        result = harness.check_file_write("agent-1", "src/auth/login.ts")
        assert result is True

        # Agent cannot write to other unit's files
        result = harness.check_file_write("agent-1", "src/catalog/product.ts")
        assert isinstance(result, Violation)
        assert result.rule_id == "MULTI-AGENT-01"

    def test_cost_benefit_assessment(self, harness):
        assessment = harness.get_cost_benefit(3)
        assert assessment["total_units"] == 4
        assert assessment["independent_units"] == 3
        assert assessment["parallelizable_fraction"] == 0.75
        assert assessment["theoretical_speedup"] == 2.0
        assert assessment["recommendation"] == "PARALLEL"

    def test_low_p_recommends_sequential(self):
        # Create a mostly sequential graph
        units = [
            Unit(name="a", owned_files=["a/**"], depends_on=[], produces=[]),
            Unit(name="b", owned_files=["b/**"], depends_on=["a"], produces=[]),
            Unit(name="c", owned_files=["c/**"], depends_on=["b"], produces=[]),
            Unit(name="d", owned_files=["d/**"], depends_on=["c"], produces=[]),
        ]
        harness = CoordinationHarness(units)
        assessment = harness.get_cost_benefit(4)
        assert assessment["parallelizable_fraction"] == 0.25
        assert assessment["recommendation"] == "SEQUENTIAL"

    def test_cycle_raises_at_construction(self):
        units = [
            Unit(name="a", owned_files=["a/**"], depends_on=["c"], produces=[]),
            Unit(name="b", owned_files=["b/**"], depends_on=["a"], produces=[]),
            Unit(name="c", owned_files=["c/**"], depends_on=["b"], produces=[]),
        ]
        with pytest.raises(ValueError, match="cycle"):
            CoordinationHarness(units)

    def test_status_report(self, harness):
        harness.assign_agent("agent-1", "auth-service", "feat/auth", "/ws/1")
        status = harness.get_status()
        assert status["units"]["auth-service"]["status"] == "in_progress"
        assert status["units"]["auth-service"]["agent"] == "agent-1"
        assert "api-gateway" not in status["ready_to_start"]  # blocked by deps

    def test_audit_log_export(self, harness):
        harness.assign_agent("agent-1", "auth-service", "feat/auth", "/ws/1")
        harness.check_file_write("agent-1", "src/catalog/product.ts")  # violation
        harness.signal_completion("agent-1")

        log = json.loads(harness.export_audit_log())
        assert log["summary"]["violations"] == 1
        assert log["summary"]["completed"] == 1
        assert len(log["events"]) == 3  # assignment + violation + completion

    def test_from_config(self):
        config = {
            "units": [
                {"name": "auth", "owned_files": ["src/auth/**"], "depends_on": [], "produces": ["JWT"]},
                {"name": "gateway", "owned_files": ["src/gw/**"], "depends_on": ["auth"], "produces": ["API"]},
            ]
        }
        harness = CoordinationHarness.from_config(config)
        assert len(harness.units) == 2
        assert harness.get_ready_units() == ["auth"]


class TestStragglerMonitor:
    """Tests for MULTI-AGENT-05 enforcement."""

    def test_no_stragglers_initially(self):
        monitor = StragglerMonitor()
        monitor.start_tracking("auth", timeout_minutes=90)
        assert monitor.check_stragglers() == []

    def test_elapsed_tracking(self):
        monitor = StragglerMonitor()
        monitor.start_tracking("auth", timeout_minutes=90)
        time.sleep(0.1)
        elapsed = monitor.get_elapsed("auth")
        assert elapsed is not None
        assert elapsed > 0

    def test_stop_tracking(self):
        monitor = StragglerMonitor()
        monitor.start_tracking("auth", timeout_minutes=90)
        monitor.stop_tracking("auth")
        assert monitor.get_elapsed("auth") is None
