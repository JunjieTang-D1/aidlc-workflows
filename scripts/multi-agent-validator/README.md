# Multi-Agent Coordination Plan Validator

A command-line tool that validates an AI-DLC project's multi-agent coordination artifacts against the 10 MULTI-AGENT extension rules.

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
