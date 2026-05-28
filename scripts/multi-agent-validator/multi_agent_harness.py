"""
Multi-Agent Coordination Harness

Runtime enforcement layer for AI-DLC multi-agent coordination rules.
Inspired by AgentCorp's GovernanceHarness pattern — provides code-enforced
gates rather than relying solely on LLM rule interpretation.

Usage:
    from multi_agent_harness import CoordinationHarness, Unit, AgentAssignment

    harness = CoordinationHarness.from_plan("aidlc-docs/multi-agent-plan.md")
    harness.validate_assignment(agent_id="agent-1", unit="auth-service")
    harness.check_file_ownership(agent_id="agent-1", file_path="src/auth/login.ts")
    harness.signal_completion(agent_id="agent-1", unit="auth-service")
    harness.check_ready_to_start(unit="api-gateway")  # checks dependencies
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class UnitStatus(StrEnum):
    """Status of a unit in the coordination plan."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"


class ViolationType(StrEnum):
    """Types of coordination violations."""
    FILE_OWNERSHIP = "file_ownership"
    DEPENDENCY_RACE = "dependency_race"
    SHARED_STATE = "shared_state"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
    CONTRACT_MODIFICATION = "contract_modification"
    CONCURRENT_BRANCH_WRITE = "concurrent_branch_write"


@dataclass
class Unit:
    """A work unit in the multi-agent plan."""
    name: str
    owned_files: list[str]  # glob patterns
    depends_on: list[str]  # unit names
    produces: list[str]  # artifact descriptions
    estimated_minutes: int = 45
    timeout_minutes: int = 90
    status: UnitStatus = UnitStatus.PENDING
    assigned_agent: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    branch: str | None = None


@dataclass
class AgentAssignment:
    """Assignment of an agent to a unit."""
    agent_id: str
    unit_name: str
    branch: str
    workspace_path: str
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class Violation:
    """A coordination rule violation."""
    timestamp: str
    violation_type: ViolationType
    agent_id: str
    unit_name: str
    rule_id: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "type": self.violation_type.value,
            "agent_id": self.agent_id,
            "unit_name": self.unit_name,
            "rule_id": self.rule_id,
            "message": self.message,
            "details": self.details,
            "blocking": self.blocking,
        }


@dataclass
class CoordinationEvent:
    """Event in the coordination lifecycle."""
    timestamp: str
    event_type: str  # assignment, completion, failure, violation, timeout
    agent_id: str
    unit_name: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "unit_name": self.unit_name,
            "data": self.data,
        }


class FileOwnershipRegistry:
    """
    Enforces MULTI-AGENT-01: File Ownership Per Unit.

    Maintains a registry of file → unit ownership and blocks
    write attempts from non-owning agents.
    """

    def __init__(self) -> None:
        self._ownership: dict[str, str] = {}  # file_pattern → unit_name
        self._lock = threading.Lock()

    def register(self, unit_name: str, file_patterns: list[str]) -> None:
        """Register file ownership for a unit."""
        with self._lock:
            for pattern in file_patterns:
                if pattern in self._ownership and self._ownership[pattern] != unit_name:
                    raise ValueError(
                        f"File pattern '{pattern}' already owned by "
                        f"'{self._ownership[pattern]}', cannot assign to '{unit_name}'"
                    )
                self._ownership[pattern] = unit_name

    def check_write_allowed(self, agent_id: str, file_path: str, agent_unit: str) -> bool:
        """
        Check if agent is allowed to write to a file.

        Returns True if allowed, False if blocked.
        """
        with self._lock:
            for pattern, owner_unit in self._ownership.items():
                if self._matches(file_path, pattern):
                    if owner_unit != agent_unit:
                        return False
            return True

    def get_owner(self, file_path: str) -> str | None:
        """Get the owning unit for a file path."""
        for pattern, owner in self._ownership.items():
            if self._matches(file_path, pattern):
                return owner
        return None

    @staticmethod
    def _matches(file_path: str, pattern: str) -> bool:
        """Simple glob-style matching (supports ** and *)."""
        import fnmatch
        # Normalize
        file_path = file_path.replace("\\", "/")
        pattern = pattern.replace("\\", "/")

        # Handle directory patterns (pattern ends with /**)
        if pattern.endswith("/**"):
            dir_prefix = pattern[:-3]
            return file_path.startswith(dir_prefix + "/") or file_path == dir_prefix
        if pattern.endswith("/*"):
            dir_prefix = pattern[:-2]
            parts = file_path.split("/")
            return "/".join(parts[:-1]) == dir_prefix

        return fnmatch.fnmatch(file_path, pattern)


class DependencyGraph:
    """
    Enforces MULTI-AGENT-02: Dependency Graph Declaration.

    Validates DAG properties and prevents dependency races.
    """

    def __init__(self) -> None:
        self._edges: dict[str, list[str]] = {}  # unit → depends_on units
        self._unit_status: dict[str, UnitStatus] = {}

    def add_unit(self, unit_name: str, depends_on: list[str]) -> None:
        """Add a unit and its dependencies."""
        self._edges[unit_name] = depends_on
        self._unit_status[unit_name] = UnitStatus.PENDING

    def validate_dag(self) -> tuple[bool, list[str]]:
        """
        Validate the graph is a DAG (no cycles).

        Returns (is_valid, cycle_path_if_invalid).
        """
        visited = set()
        rec_stack = set()
        cycle_path: list[str] = []

        def _dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for dep in self._edges.get(node, []):
                if dep not in visited:
                    if _dfs(dep):
                        cycle_path.append(dep)
                        return True
                elif dep in rec_stack:
                    cycle_path.append(dep)
                    return True
            rec_stack.discard(node)
            return False

        for unit in self._edges:
            if unit not in visited:
                if _dfs(unit):
                    cycle_path.append(unit)
                    return False, list(reversed(cycle_path))

        return True, []

    def can_start(self, unit_name: str) -> tuple[bool, list[str]]:
        """
        Check if a unit's dependencies are satisfied.

        Returns (can_start, list_of_unsatisfied_dependencies).
        """
        deps = self._edges.get(unit_name, [])
        unsatisfied = [
            dep for dep in deps
            if self._unit_status.get(dep) != UnitStatus.COMPLETED
        ]
        return len(unsatisfied) == 0, unsatisfied

    def mark_completed(self, unit_name: str) -> None:
        """Mark a unit as completed."""
        self._unit_status[unit_name] = UnitStatus.COMPLETED

    def mark_failed(self, unit_name: str) -> None:
        """Mark a unit as failed."""
        self._unit_status[unit_name] = UnitStatus.FAILED

    def get_independent_units(self) -> list[str]:
        """Get units that can run in parallel (no unmet dependencies)."""
        return [
            unit for unit in self._edges
            if self._unit_status[unit] == UnitStatus.PENDING
            and self.can_start(unit)[0]
        ]

    def parallelizable_fraction(self) -> float:
        """
        Estimate p (parallelizable fraction).

        p = max_parallel_batch_size / total_units
        """
        total = len(self._edges)
        if total == 0:
            return 0.0
        independent = len(self.get_independent_units())
        return independent / total

    def amdahl_speedup(self, n_agents: int) -> float:
        """Calculate theoretical speedup with n agents using Amdahl's Law."""
        p = self.parallelizable_fraction()
        if n_agents <= 0:
            return 1.0
        return 1.0 / ((1 - p) + p / n_agents)


class StragglerMonitor:
    """
    Enforces MULTI-AGENT-05: Straggler Detection and Timeout Policy.

    Monitors running units and triggers timeout actions.
    """

    def __init__(self) -> None:
        self._start_times: dict[str, float] = {}  # unit → start timestamp
        self._timeouts: dict[str, int] = {}  # unit → timeout minutes
        self._callbacks: dict[str, Any] = {}  # unit → timeout callback
        self._lock = threading.Lock()

    def start_tracking(self, unit_name: str, timeout_minutes: int) -> None:
        """Start tracking a unit for straggler detection."""
        with self._lock:
            self._start_times[unit_name] = time.time()
            self._timeouts[unit_name] = timeout_minutes

    def stop_tracking(self, unit_name: str) -> None:
        """Stop tracking a unit (completed or killed)."""
        with self._lock:
            self._start_times.pop(unit_name, None)
            self._timeouts.pop(unit_name, None)

    def check_stragglers(self) -> list[tuple[str, float]]:
        """
        Check for units that exceeded their timeout.

        Returns list of (unit_name, minutes_over_timeout).
        """
        stragglers = []
        now = time.time()
        with self._lock:
            for unit, start in self._start_times.items():
                elapsed_min = (now - start) / 60
                timeout = self._timeouts.get(unit, 90)
                if elapsed_min > timeout:
                    stragglers.append((unit, elapsed_min - timeout))
        return stragglers

    def get_elapsed(self, unit_name: str) -> float | None:
        """Get elapsed minutes for a tracked unit."""
        with self._lock:
            start = self._start_times.get(unit_name)
            if start is None:
                return None
            return (time.time() - start) / 60


class CoordinationHarness:
    """
    Main orchestration harness for multi-agent coordination.

    Combines file ownership, dependency graph, straggler monitoring,
    and violation tracking into a single coordination point.

    Analogous to AgentCorp's GovernanceHarness but focused on
    coordination constraints rather than content governance.
    """

    def __init__(self, units: list[Unit]) -> None:
        self.units = {u.name: u for u in units}
        self.file_registry = FileOwnershipRegistry()
        self.dependency_graph = DependencyGraph()
        self.straggler_monitor = StragglerMonitor()
        self.violations: list[Violation] = []
        self.events: list[CoordinationEvent] = []
        self.assignments: dict[str, AgentAssignment] = {}  # agent_id → assignment
        self._lock = threading.Lock()

        # Initialize registries
        for unit in units:
            self.file_registry.register(unit.name, unit.owned_files)
            self.dependency_graph.add_unit(unit.name, unit.depends_on)

        # Validate DAG at construction time
        is_valid, cycle = self.dependency_graph.validate_dag()
        if not is_valid:
            raise ValueError(f"Dependency graph has cycle: {' → '.join(cycle)}")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CoordinationHarness":
        """Create harness from structured configuration."""
        units = []
        for unit_config in config.get("units", []):
            units.append(Unit(
                name=unit_config["name"],
                owned_files=unit_config.get("owned_files", []),
                depends_on=unit_config.get("depends_on", []),
                produces=unit_config.get("produces", []),
                estimated_minutes=unit_config.get("estimated_minutes", 45),
                timeout_minutes=unit_config.get("timeout_minutes", 90),
            ))
        return cls(units)

    def assign_agent(self, agent_id: str, unit_name: str, branch: str, workspace: str) -> AgentAssignment | Violation:
        """
        Assign an agent to a unit. Enforces MULTI-AGENT-02 (dependency check).

        Returns AgentAssignment on success, Violation if dependencies not met.
        """
        with self._lock:
            unit = self.units.get(unit_name)
            if unit is None:
                raise ValueError(f"Unknown unit: {unit_name}")

            # Check dependencies (MULTI-AGENT-02)
            can_start, unsatisfied = self.dependency_graph.can_start(unit_name)
            if not can_start:
                violation = Violation(
                    timestamp=datetime.now(UTC).isoformat(),
                    violation_type=ViolationType.DEPENDENCY_RACE,
                    agent_id=agent_id,
                    unit_name=unit_name,
                    rule_id="MULTI-AGENT-02",
                    message=f"Cannot start '{unit_name}': dependencies not met",
                    details={"unsatisfied": unsatisfied},
                )
                self.violations.append(violation)
                self._record_event("violation", agent_id, unit_name, violation.to_dict())
                return violation

            # Create assignment
            assignment = AgentAssignment(
                agent_id=agent_id,
                unit_name=unit_name,
                branch=branch,
                workspace_path=workspace,
            )
            self.assignments[agent_id] = assignment
            unit.status = UnitStatus.IN_PROGRESS
            unit.assigned_agent = agent_id
            unit.started_at = assignment.started_at
            unit.branch = branch

            # Start straggler monitoring (MULTI-AGENT-05)
            self.straggler_monitor.start_tracking(unit_name, unit.timeout_minutes)

            self._record_event("assignment", agent_id, unit_name, {
                "branch": branch, "workspace": workspace
            })

            return assignment

    def check_file_write(self, agent_id: str, file_path: str) -> bool | Violation:
        """
        Check if an agent can write to a file. Enforces MULTI-AGENT-01.

        Returns True if allowed, or Violation if blocked.
        """
        assignment = self.assignments.get(agent_id)
        if assignment is None:
            raise ValueError(f"Agent '{agent_id}' has no assignment")

        allowed = self.file_registry.check_write_allowed(
            agent_id, file_path, assignment.unit_name
        )

        if not allowed:
            owner = self.file_registry.get_owner(file_path)
            violation = Violation(
                timestamp=datetime.now(UTC).isoformat(),
                violation_type=ViolationType.FILE_OWNERSHIP,
                agent_id=agent_id,
                unit_name=assignment.unit_name,
                rule_id="MULTI-AGENT-01",
                message=f"Agent '{agent_id}' cannot write to '{file_path}' (owned by '{owner}')",
                details={"file_path": file_path, "owner_unit": owner},
            )
            self.violations.append(violation)
            self._record_event("violation", agent_id, assignment.unit_name, violation.to_dict())
            return violation

        return True

    def signal_completion(self, agent_id: str) -> None:
        """Signal that an agent has completed its unit."""
        with self._lock:
            assignment = self.assignments.get(agent_id)
            if assignment is None:
                raise ValueError(f"Agent '{agent_id}' has no assignment")

            unit = self.units[assignment.unit_name]
            unit.status = UnitStatus.COMPLETED
            unit.completed_at = datetime.now(UTC).isoformat()

            self.dependency_graph.mark_completed(assignment.unit_name)
            self.straggler_monitor.stop_tracking(assignment.unit_name)

            self._record_event("completion", agent_id, assignment.unit_name, {
                "elapsed_minutes": self.straggler_monitor.get_elapsed(assignment.unit_name)
            })

    def signal_failure(self, agent_id: str, reason: str) -> None:
        """Signal that an agent has failed its unit."""
        with self._lock:
            assignment = self.assignments.get(agent_id)
            if assignment is None:
                raise ValueError(f"Agent '{agent_id}' has no assignment")

            unit = self.units[assignment.unit_name]
            unit.status = UnitStatus.FAILED

            self.dependency_graph.mark_failed(assignment.unit_name)
            self.straggler_monitor.stop_tracking(assignment.unit_name)

            self._record_event("failure", agent_id, assignment.unit_name, {"reason": reason})

    def get_ready_units(self) -> list[str]:
        """Get units that are ready to start (all dependencies met)."""
        return self.dependency_graph.get_independent_units()

    def get_cost_benefit(self, n_agents: int) -> dict[str, Any]:
        """
        Generate MULTI-AGENT-08 cost-benefit assessment.

        Returns structured assessment with Amdahl's calculation.
        """
        p = self.dependency_graph.parallelizable_fraction()
        speedup = self.dependency_graph.amdahl_speedup(n_agents)
        independent = self.dependency_graph.get_independent_units()

        recommendation = "PARALLEL"
        if p < 0.5:
            recommendation = "SEQUENTIAL"
        elif p < 0.6:
            recommendation = "MARGINAL — consider sequential"

        return {
            "total_units": len(self.units),
            "independent_units": len(independent),
            "parallelizable_fraction": round(p, 3),
            "planned_agents": n_agents,
            "theoretical_speedup": round(speedup, 2),
            "recommendation": recommendation,
            "reasoning": (
                f"p={p:.1%}, {len(independent)} independent units out of {len(self.units)}. "
                f"With {n_agents} agents, theoretical speedup is {speedup:.2f}x. "
                f"{'Parallelism recommended.' if p >= 0.6 else 'Sequential likely faster due to coordination overhead.'}"
            )
        }

    def get_status(self) -> dict[str, Any]:
        """Get current coordination status summary."""
        stragglers = self.straggler_monitor.check_stragglers()
        return {
            "units": {
                name: {
                    "status": unit.status.value,
                    "agent": unit.assigned_agent,
                    "branch": unit.branch,
                    "elapsed_minutes": self.straggler_monitor.get_elapsed(name),
                }
                for name, unit in self.units.items()
            },
            "violations": len(self.violations),
            "stragglers": [{"unit": s[0], "minutes_over": round(s[1], 1)} for s in stragglers],
            "ready_to_start": self.get_ready_units(),
            "events_count": len(self.events),
        }

    def export_audit_log(self, path: Path | None = None) -> str:
        """Export full audit log as JSON."""
        log = {
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_units": len(self.units),
                "completed": sum(1 for u in self.units.values() if u.status == UnitStatus.COMPLETED),
                "failed": sum(1 for u in self.units.values() if u.status == UnitStatus.FAILED),
                "violations": len(self.violations),
            },
            "violations": [v.to_dict() for v in self.violations],
            "events": [e.to_dict() for e in self.events],
        }
        output = json.dumps(log, indent=2)
        if path:
            path.write_text(output, encoding="utf-8")
        return output

    def _record_event(self, event_type: str, agent_id: str, unit_name: str, data: dict) -> None:
        """Record a coordination event."""
        self.events.append(CoordinationEvent(
            timestamp=datetime.now(UTC).isoformat(),
            event_type=event_type,
            agent_id=agent_id,
            unit_name=unit_name,
            data=data,
        ))
