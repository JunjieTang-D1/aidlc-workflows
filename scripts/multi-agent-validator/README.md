# Multi-Agent Coordination Plan Validator

A command-line tool that validates an AI-DLC project's multi-agent coordination artifacts against the 10 blocking MULTI-AGENT extension rules, plus an opt-in advisory speculative-branching layer (MULTI-AGENT-11).

## Components

| File | Purpose | Status |
|---|---|---|
| `validate_multi_agent_plan.py` | Static plan validator — checks markdown artifacts for compliance with rules 01–10 | Blocking (default) |
| `multi_agent_harness.py` | Runtime coordination harness — file ownership, DAG, straggler monitor, audit log | Library |
| `speculative_branching.py` | EVOI-gated speculative-branching layer — uncertainty signal, fork gate, Pareto winner selection, persistent history (MULTI-AGENT-11) | Advisory, opt-in |

## Installation

No dependencies required — uses Python 3.10+ standard library only.

```bash
chmod +x validate_multi_agent_plan.py
```

## Usage

```bash
# Validate all 10 rules
python validate_multi_agent_plan.py ./aidlc-docs

# Partial enforcement (rules 01, 02, 03 only)
python validate_multi_agent_plan.py ./aidlc-docs --partial

# JSON output (for CI/CD integration)
python validate_multi_agent_plan.py ./aidlc-docs --json

# Strict mode (N/A treated as failures)
python validate_multi_agent_plan.py ./aidlc-docs --strict
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All applicable rules pass |
| 1 | One or more blocking findings |
| 2 | Missing required artifacts |

## What It Checks

The validator scans your `aidlc-docs/` directory for:

| Rule | Looks For |
|------|-----------|
| 01 | "Owned Files" or "File Scope" sections per unit |
| 02 | Dependency graph (DAG, Mermaid, produces/consumes) |
| 03 | Interface contracts (OpenAPI, schemas, contracts/ dir) |
| 04 | Isolation strategy (worktree, branch, container) |
| 05 | Timeout policy (estimated time, hard timeout, straggler handling) |
| 06 | Shared state documentation (read-only declarations) |
| 07 | Integration verification step (post-merge build + test) |
| 08 | Cost-benefit assessment (p estimate, speedup calculation) |
| 09 | Communication protocol (channels, message format, orchestrator) |
| 10 | Rollback plan (recovery procedures, retry policy) |

## Example Output

```
======================================================================
  MULTI-AGENT COORDINATION PLAN VALIDATION REPORT
======================================================================

  ✓ MULTI-AGENT-01: File Ownership Per Unit
    File ownership declarations found (4 units detected)

  ✓ MULTI-AGENT-02: Dependency Graph Declaration
    Dependency graph declaration found
      • Dependency graph detected
      • Parallelizable fraction (p) estimate found

  ✗ MULTI-AGENT-03: Interface Contracts Before Parallel Execution [BLOCKING]
    No interface contracts found for cross-unit integration
      • Expected: contracts/ directory or interface documentation
      • Cross-unit interfaces must be defined before parallel execution

  ...

----------------------------------------------------------------------
  Summary: 7 passed, 2 failed, 1 N/A, 0 skipped (of 10 rules)

  ❌ RESULT: BLOCKING FINDINGS — resolve before proceeding
======================================================================
```

## CI/CD Integration

```yaml
# GitHub Actions example
- name: Validate Multi-Agent Plan
  run: python scripts/multi-agent-validator/validate_multi_agent_plan.py ./aidlc-docs --json
```

## Extending

The validator uses pattern matching against markdown files. To add custom patterns for your project's naming conventions, modify the `*_patterns` lists in the validator functions.

## Speculative Branching (MULTI-AGENT-11, advisory)

`speculative_branching.py` adds an EVOI-gated branching layer for **high-uncertainty units on the critical path** — projects where one-shot-then-blind-retry has historically wasted sprint time. It is a deterministic library (no LLM dependency, mirroring AgentCorp V13), **opt-in**, and **never blocking**.

```python
from speculative_branching import (
    SpeculativeDispatcher, UnitHistoryStore, UnitDescriptor,
    EVOIGate, BranchResult,
)

# 1. Persistent attempt log — survives across sprints so the system learns.
history = UnitHistoryStore(Path("aidlc-docs/.speculative-history.json"))

# 2. Conservative gate: fork only when EVOI > 0 AND budget headroom holds.
dispatcher = SpeculativeDispatcher(
    history,
    gate=EVOIGate(n_branches=3, per_branch_cost_usd=1.5, min_uncertainty=0.35),
)

# 3. Plan dispatch for a tricky unit (e.g. dealscope DS-009 governance modes).
unit = UnitDescriptor(
    name="governance-modes",
    depends_on=["audit-log", "phase-orchestrator"],
    owned_files=["src/governance/**", "tests/test_governance.py"],
    estimated_minutes=180,
    acceptance_criteria_count=4,
    on_critical_path=True,
)
plan = dispatcher.plan(unit, budget_remaining_usd=20.0)
if plan.fork:
    # Hand `plan.branches` (3 isolated workspaces, distinct preambles)
    # to your runtime — Strands, GitHub Actions, IDE composer, etc.
    pass

# 4. After branches finish, record outcomes and Pareto-pick the winner.
results = [BranchResult(...), BranchResult(...), BranchResult(...)]
selection = dispatcher.record_outcomes("governance-modes", results)
print(selection.winner.branch_id, selection.rationale)
```

The dispatcher does not run agents itself — it returns a `DispatchPlan` with branch IDs, suggested git branch names, isolated workspace subdirectories, and per-branch instruction preambles. The orchestrator (human or coordinating agent) executes them via whatever runtime is in use. After branches complete, every outcome — winner *and* losers — is persisted to `UnitHistoryStore`, so the next sprint's uncertainty signal reflects the full sample.

See `docs/speculative-branching.md` for the full design rationale, EVOI formula, and a worked dealscope example.
