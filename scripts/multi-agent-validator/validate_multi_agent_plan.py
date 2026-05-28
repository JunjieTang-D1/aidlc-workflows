#!/usr/bin/env python3
"""
Multi-Agent Coordination Plan Validator

Validates an AI-DLC project's multi-agent coordination artifacts against
the MULTI-AGENT extension rules. Checks file ownership declarations,
dependency graphs, interface contracts, timeout policies, and cost-benefit
assessments.

Usage:
    python validate_multi_agent_plan.py <aidlc-docs-path>
    python validate_multi_agent_plan.py ./aidlc-docs --strict
    python validate_multi_agent_plan.py ./aidlc-docs --partial  # only rules 01,02,03

Exit codes:
    0 - All applicable rules pass
    1 - One or more blocking findings
    2 - Missing required artifacts (cannot validate)
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class RuleStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "N/A"
    SKIP = "SKIP"


class Severity(Enum):
    BLOCKING = "BLOCKING"
    ADVISORY = "ADVISORY"


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    status: RuleStatus
    severity: Severity
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    findings: list[Finding] = field(default_factory=list)
    total_rules: int = 0
    passed: int = 0
    failed: int = 0
    na: int = 0
    skipped: int = 0

    @property
    def has_blocking_failures(self) -> bool:
        return any(
            f.status == RuleStatus.FAIL and f.severity == Severity.BLOCKING
            for f in self.findings
        )


RULES = [
    ("MULTI-AGENT-01", "File Ownership Per Unit"),
    ("MULTI-AGENT-02", "Dependency Graph Declaration"),
    ("MULTI-AGENT-03", "Interface Contracts Before Parallel Execution"),
    ("MULTI-AGENT-04", "Isolated Workspace Per Agent"),
    ("MULTI-AGENT-05", "Straggler Detection and Timeout Policy"),
    ("MULTI-AGENT-06", "No Shared Mutable State During Parallel Execution"),
    ("MULTI-AGENT-07", "Integration Verification After Merge"),
    ("MULTI-AGENT-08", "Parallelism Cost-Benefit Assessment"),
    ("MULTI-AGENT-09", "Communication Protocol"),
    ("MULTI-AGENT-10", "Rollback and Recovery Plan"),
]

PARTIAL_RULES = {"MULTI-AGENT-01", "MULTI-AGENT-02", "MULTI-AGENT-03"}


def find_file(base: Path, patterns: list[str]) -> Optional[Path]:
    """Find first matching file from list of possible paths."""
    for pattern in patterns:
        candidates = list(base.glob(pattern))
        if candidates:
            return candidates[0]
    return None


def read_file_content(path: Path) -> str:
    """Read file content, return empty string if not found."""
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def find_units(base: Path) -> list[str]:
    """Discover unit names from aidlc-docs structure."""
    units = []
    # Check units.md and plan files
    plan_files = [
        find_file(base, ["inception/units.md", "units.md"]),
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
    ]

    unit_patterns = [
        r"##\s+Unit\s*\d*:\s*(.+)",
        r"[-*]\s+\*\*Unit\s*\d*:\s*(.+?)\*\*",
        r"[-*]\s+Unit\s*\d*:\s*(.+)",
        # Table rows with unit names: | unit-name | ...
        r"\|\s*([a-z][a-z0-9_-]+)\s*\|",
        # Agent assignment patterns: Agent N works on unit-name
        r"(?:auth|product|notification|api|user|order|payment|catalog|gateway)[a-z_-]*(?:-service)?",
    ]

    for pf in plan_files:
        if pf:
            content = read_file_content(pf)
            for pattern in unit_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    # Filter out table headers and common non-unit strings
                    filtered = [
                        m.strip() for m in matches
                        if m.strip() and m.strip().lower() not in (
                            "unit", "owned files", "agent", "branch",
                            "estimated time", "hard timeout", "straggler action",
                            "---", "failure scenario", "recovery action",
                            "strategy", "merge strategy"
                        ) and len(m.strip()) > 2
                    ]
                    units.extend(filtered)

    # Check construction subdirectories
    construction = base / "construction"
    if construction.exists():
        for d in construction.iterdir():
            if d.is_dir() and d.name not in ("plans", "shared", "build-and-test"):
                units.append(d.name)

    # Deduplicate and return
    return list(set(units)) if units else []


def validate_rule_01(base: Path, units: list[str]) -> Finding:
    """MULTI-AGENT-01: File Ownership Per Unit"""
    rule_id = "MULTI-AGENT-01"
    rule_name = "File Ownership Per Unit"

    # Look for ownership declarations
    ownership_patterns = [
        "owned files", "file ownership", "file scope",
        "owns:", "ownership matrix", "assigned files"
    ]

    # Check units.md, execution-plan, or workflow-plan
    plan_files = [
        find_file(base, ["inception/units.md", "units.md"]),
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
    ]

    ownership_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in ownership_patterns):
                ownership_found = True
                break

    if not units:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.NA, severity=Severity.BLOCKING,
            message="No units detected — cannot validate file ownership"
        )

    if not ownership_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No file ownership declarations found for units",
            details=[
                f"Expected 'Owned Files' or 'File Scope' section for each of {len(units)} units",
                "Check: inception/units.md, inception/plans/execution-plan.md, or multi-agent-plan.md"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message=f"File ownership declarations found ({len(units)} units detected)"
    )


def validate_rule_02(base: Path, units: list[str]) -> Finding:
    """MULTI-AGENT-02: Dependency Graph Declaration"""
    rule_id = "MULTI-AGENT-02"
    rule_name = "Dependency Graph Declaration"

    dependency_patterns = [
        "dependency graph", "dependency dag", "depends on",
        "dependencies:", "produces:", "consumes:",
        "parallelizable fraction", "mermaid", "graph TD", "graph LR",
        "independent units"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["inception/units.md", "units.md"]),
    ]

    dag_found = False
    has_p_estimate = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in dependency_patterns[:6]):
                dag_found = True
            if "parallelizable fraction" in content or "p =" in content or "p=" in content:
                has_p_estimate = True

    if not units:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.NA, severity=Severity.BLOCKING,
            message="No units detected — cannot validate dependency graph"
        )

    if not dag_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No dependency graph found",
            details=[
                "Expected: DAG showing which units depend on others",
                "Expected: Each unit declares what it produces and consumes",
                "Check: execution-plan.md, workflow-plan.md, or multi-agent-plan.md"
            ]
        )

    details = ["Dependency graph detected"]
    if has_p_estimate:
        details.append("Parallelizable fraction (p) estimate found")
    else:
        details.append("WARNING: No parallelizable fraction estimate (recommended)")

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Dependency graph declaration found",
        details=details
    )


def validate_rule_03(base: Path) -> Finding:
    """MULTI-AGENT-03: Interface Contracts Before Parallel Execution"""
    rule_id = "MULTI-AGENT-03"
    rule_name = "Interface Contracts Before Parallel Execution"

    contract_patterns = [
        "interface contract", "api contract", "schema",
        "openapi", "graphql", "protobuf", "grpc",
        "shared types", "contract-first"
    ]

    # Check for contracts directory or contract docs
    contracts_dir = base / "contracts"
    has_contracts_dir = contracts_dir.exists() and any(contracts_dir.iterdir()) if contracts_dir.exists() else False

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["inception/application-design/*.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["contracts/*.md"]),
    ]

    contracts_documented = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in contract_patterns):
                contracts_documented = True
                break

    if has_contracts_dir or contracts_documented:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.PASS, severity=Severity.BLOCKING,
            message="Interface contracts found"
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.FAIL, severity=Severity.BLOCKING,
        message="No interface contracts found for cross-unit integration",
        details=[
            "Expected: contracts/ directory or interface documentation in design artifacts",
            "Cross-unit interfaces must be defined before parallel execution begins"
        ]
    )


def validate_rule_04(base: Path) -> Finding:
    """MULTI-AGENT-04: Isolated Workspace Per Agent"""
    rule_id = "MULTI-AGENT-04"
    rule_name = "Isolated Workspace Per Agent"

    isolation_patterns = [
        "worktree", "feature branch", "isolation strategy",
        "branch per agent", "subdirectory isolation",
        "container", "sandbox", "merge strategy"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
    ]

    isolation_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in isolation_patterns):
                isolation_found = True
                break

    if not isolation_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No workspace isolation strategy documented",
            details=[
                "Expected: worktree, branch-per-agent, subdirectory, or container isolation",
                "Must also define merge strategy after parallel completion"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Workspace isolation strategy documented"
    )


def validate_rule_05(base: Path, units: list[str]) -> Finding:
    """MULTI-AGENT-05: Straggler Detection and Timeout Policy"""
    rule_id = "MULTI-AGENT-05"
    rule_name = "Straggler Detection and Timeout Policy"

    timeout_patterns = [
        "timeout", "straggler", "estimated completion",
        "hard timeout", "kill and reassign", "progress checkpoint",
        "wall-clock", "maximum time"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
    ]

    timeout_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in timeout_patterns):
                timeout_found = True
                break

    if not units:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.NA, severity=Severity.BLOCKING,
            message="No units detected — cannot validate timeout policy"
        )

    if not timeout_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No straggler detection or timeout policy found",
            details=[
                "Each parallel unit must have estimated completion time + hard timeout",
                "Must define straggler handling: kill/reassign, kill/skip, or extend"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Timeout policy documented"
    )


def validate_rule_06(base: Path) -> Finding:
    """MULTI-AGENT-06: No Shared Mutable State During Parallel Execution"""
    rule_id = "MULTI-AGENT-06"
    rule_name = "No Shared Mutable State During Parallel Execution"

    # This rule is harder to validate statically — check for documentation
    state_patterns = [
        "shared state", "mutable state", "shared database",
        "concurrent write", "read-only", "append-only",
        "no shared mutable", "state isolation"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
    ]

    state_addressed = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in state_patterns):
                state_addressed = True
                break

    if not state_addressed:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No shared state policy documented",
            details=[
                "Must document: what resources are shared, which are read-only",
                "Agents must not write to same mutable resource concurrently"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Shared state policy documented"
    )


def validate_rule_07(base: Path) -> Finding:
    """MULTI-AGENT-07: Integration Verification After Merge"""
    rule_id = "MULTI-AGENT-07"
    rule_name = "Integration Verification After Merge"

    integration_patterns = [
        "integration verification", "integration test",
        "post-merge", "after merge", "combined build",
        "integration step", "merge verification"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["construction/build-and-test/*.md"]),
    ]

    integration_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in integration_patterns):
                integration_found = True
                break

    if not integration_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No integration verification step found after merge",
            details=[
                "Must include: build success, unit tests pass, integration tests pass",
                "Failures must be attributed to specific units"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Integration verification step documented"
    )


def validate_rule_08(base: Path) -> Finding:
    """MULTI-AGENT-08: Parallelism Cost-Benefit Assessment"""
    rule_id = "MULTI-AGENT-08"
    rule_name = "Parallelism Cost-Benefit Assessment"

    cost_benefit_patterns = [
        "cost-benefit", "amdahl", "speedup",
        "parallelizable fraction", "coordination overhead",
        "parallel vs sequential", "independent units"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
    ]

    assessment_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in cost_benefit_patterns):
                assessment_found = True
                break

    if not assessment_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No parallelism cost-benefit assessment found",
            details=[
                "Must estimate parallelizable fraction (p)",
                "Must calculate theoretical speedup for planned agent count",
                "Must document decision: parallel vs sequential with rationale"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Cost-benefit assessment documented"
    )


def validate_rule_09(base: Path) -> Finding:
    """MULTI-AGENT-09: Communication Protocol"""
    rule_id = "MULTI-AGENT-09"
    rule_name = "Communication Protocol"

    comm_patterns = [
        "communication protocol", "communication channel",
        "orchestrator", "status update", "message format",
        "centralized coordinator", "event-based", "pr-based"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
    ]

    comm_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in comm_patterns):
                comm_found = True
                break

    if not comm_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No communication protocol documented",
            details=[
                "Must define: communication channels, message format, orchestrator role",
                "Agents must not directly modify each other's workspaces"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Communication protocol documented"
    )


def validate_rule_10(base: Path) -> Finding:
    """MULTI-AGENT-10: Rollback and Recovery Plan"""
    rule_id = "MULTI-AGENT-10"
    rule_name = "Rollback and Recovery Plan"

    recovery_patterns = [
        "rollback", "recovery", "retry", "partial failure",
        "revert", "independently committable", "failed unit"
    ]

    plan_files = [
        find_file(base, ["inception/plans/execution-plan.md"]),
        find_file(base, ["multi-agent-plan.md"]),
        find_file(base, ["inception/plans/workflow-plan.md"]),
    ]

    recovery_found = False
    for pf in plan_files:
        if pf:
            content = read_file_content(pf).lower()
            if any(p in content for p in recovery_patterns):
                recovery_found = True
                break

    if not recovery_found:
        return Finding(
            rule_id=rule_id, rule_name=rule_name,
            status=RuleStatus.FAIL, severity=Severity.BLOCKING,
            message="No rollback and recovery plan found",
            details=[
                "Must define: recovery from single unit failure, integration failure, straggler timeout",
                "Each unit's output must be independently revertible"
            ]
        )

    return Finding(
        rule_id=rule_id, rule_name=rule_name,
        status=RuleStatus.PASS, severity=Severity.BLOCKING,
        message="Rollback and recovery plan documented"
    )


def validate(base_path: str, partial: bool = False, output_json: bool = False) -> ValidationResult:
    """Run all validations against aidlc-docs directory."""
    base = Path(base_path)
    if not base.exists():
        print(f"ERROR: Path not found: {base_path}", file=sys.stderr)
        sys.exit(2)

    result = ValidationResult()
    units = find_units(base)

    validators = [
        (validate_rule_01, (base, units)),
        (validate_rule_02, (base, units)),
        (validate_rule_03, (base,)),
        (validate_rule_04, (base,)),
        (validate_rule_05, (base, units)),
        (validate_rule_06, (base,)),
        (validate_rule_07, (base,)),
        (validate_rule_08, (base,)),
        (validate_rule_09, (base,)),
        (validate_rule_10, (base,)),
    ]

    for i, (validator, args) in enumerate(validators):
        rule_id = RULES[i][0]

        if partial and rule_id not in PARTIAL_RULES:
            finding = Finding(
                rule_id=rule_id, rule_name=RULES[i][1],
                status=RuleStatus.SKIP, severity=Severity.ADVISORY,
                message="Skipped (partial enforcement mode)"
            )
        else:
            finding = validator(*args)

        result.findings.append(finding)
        result.total_rules += 1

        if finding.status == RuleStatus.PASS:
            result.passed += 1
        elif finding.status == RuleStatus.FAIL:
            result.failed += 1
        elif finding.status == RuleStatus.NA:
            result.na += 1
        elif finding.status == RuleStatus.SKIP:
            result.skipped += 1

    return result


def print_report(result: ValidationResult, output_json: bool = False):
    """Print validation report."""
    if output_json:
        report = {
            "summary": {
                "total": result.total_rules,
                "passed": result.passed,
                "failed": result.failed,
                "na": result.na,
                "skipped": result.skipped,
                "blocking_failures": result.has_blocking_failures,
            },
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "status": f.status.value,
                    "severity": f.severity.value,
                    "message": f.message,
                    "details": f.details,
                }
                for f in result.findings
            ],
        }
        print(json.dumps(report, indent=2))
        return

    # Text report
    print("=" * 70)
    print("  MULTI-AGENT COORDINATION PLAN VALIDATION REPORT")
    print("=" * 70)
    print()

    status_icons = {
        RuleStatus.PASS: "✓",
        RuleStatus.FAIL: "✗",
        RuleStatus.NA: "—",
        RuleStatus.SKIP: "○",
    }

    for finding in result.findings:
        icon = status_icons[finding.status]
        severity_tag = f" [{finding.severity.value}]" if finding.status == RuleStatus.FAIL else ""
        print(f"  {icon} {finding.rule_id}: {finding.rule_name}{severity_tag}")
        print(f"    {finding.message}")
        for detail in finding.details:
            print(f"      • {detail}")
        print()

    print("-" * 70)
    print(f"  Summary: {result.passed} passed, {result.failed} failed, "
          f"{result.na} N/A, {result.skipped} skipped (of {result.total_rules} rules)")
    print()

    if result.has_blocking_failures:
        print("  ❌ RESULT: BLOCKING FINDINGS — resolve before proceeding")
    else:
        print("  ✅ RESULT: ALL CHECKS PASSED")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Validate multi-agent coordination plan against AI-DLC extension rules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s ./aidlc-docs
    %(prog)s ./aidlc-docs --partial
    %(prog)s ./aidlc-docs --json
    %(prog)s ./aidlc-docs --strict
        """
    )
    parser.add_argument("path", help="Path to aidlc-docs directory")
    parser.add_argument("--partial", action="store_true",
                        help="Partial enforcement (rules 01, 02, 03 only)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="Treat N/A as failures (strict mode)")

    args = parser.parse_args()

    result = validate(args.path, partial=args.partial)

    if args.strict:
        for finding in result.findings:
            if finding.status == RuleStatus.NA:
                finding.status = RuleStatus.FAIL
                finding.message += " (strict mode: N/A treated as FAIL)"
                result.na -= 1
                result.failed += 1

    print_report(result, output_json=args.json)

    if result.has_blocking_failures:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
