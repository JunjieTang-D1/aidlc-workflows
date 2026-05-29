# Multi-Agent Coordination Rules

## Overview

These multi-agent coordination rules are cross-cutting constraints that apply when AI-DLC units are developed concurrently by multiple AI coding agents (or multiple instances of the same agent). They prevent the empirically-documented failure modes of parallel agent development: write conflicts, silent rewrites, dependency races, and straggler-induced bottlenecks.

These rules are grounded in Amdahl's Law for Agent Teams — the theoretical speedup from parallelizing N agents is bounded by `1 / ((1-p) + p/N)` where `p` is the parallelizable fraction of work. When coordination overhead (conflicts, rewrites, communication) erodes `p`, adding agents produces negative returns. These rules maximize effective `p` by eliminating coordination anti-patterns at the workflow level.

**Enforcement**: At each applicable stage, the model MUST verify compliance with these rules before presenting the stage completion message to the user.

### Blocking Multi-Agent Finding Behavior

A **blocking multi-agent finding** means:
1. The finding MUST be listed in the stage completion message under a "Multi-Agent Findings" section with the MULTI-AGENT rule ID and description
2. The stage MUST NOT present the "Continue to Next Stage" option until all blocking findings are resolved
3. The model MUST present only the "Request Changes" option with a clear explanation of what needs to change
4. The finding MUST be logged in `aidlc-docs/audit.md` with the MULTI-AGENT rule ID, description, and stage context

If a MULTI-AGENT rule is not applicable to the current project (e.g., only one unit exists), mark it as **N/A** in the compliance summary — this is not a blocking finding.

### Default Enforcement

All rules in this document are **blocking** by default. If any rule's verification criteria are not met, it is a blocking multi-agent finding — follow the blocking finding behavior defined above.

### Partial Enforcement Mode

If the user selected **Partial** enforcement during opt-in, only rules MULTI-AGENT-01, MULTI-AGENT-02, and MULTI-AGENT-03 are enforced. All other rules are treated as advisory (non-blocking). Log the enforcement mode in `aidlc-docs/aidlc-state.md` under `## Extension Configuration`.

### Verification Criteria Format

Verification items in this document are plain bullet points describing compliance checks. Each item should be evaluated as compliant or non-compliant during review.

---

## Rule MULTI-AGENT-01: Explicit File Ownership Per Unit

**Rule**: Every unit in the workflow plan MUST declare an exclusive file ownership set — the specific files and directories that the unit is allowed to create or modify. No two concurrently-executing units may claim ownership of the same file or directory.

Requirements:
- The ownership declaration MUST be documented in `aidlc-docs/units.md` or the unit planning artifact
- Ownership is at file or directory level (directory ownership implies all files within)
- Shared files (e.g., `package.json`, `requirements.txt`, root configuration) MUST be assigned to exactly ONE unit, or deferred to a dedicated integration unit that runs after all parallel units complete
- The orchestrator (human or coordinating agent) MUST verify no ownership overlaps before authorizing parallel execution

**Verification**:
- Every unit in the workflow plan has an explicit "Owned Files" or "File Scope" section
- No file or directory appears in the ownership set of more than one concurrently-executing unit
- Shared dependency files (lock files, manifests) are assigned to a single owner or an integration unit
- The ownership matrix is documented and auditable in `aidlc-docs/audit.md`

---

## Rule MULTI-AGENT-02: Dependency Graph Declaration

**Rule**: Before parallel execution begins, the workflow plan MUST include an explicit dependency graph declaring which units depend on the outputs of other units. Units with unresolved dependencies MUST NOT execute in parallel with their dependency providers.

Requirements:
- Each unit MUST declare: (a) what it produces (artifacts, interfaces, exports) and (b) what it consumes from other units
- The dependency graph MUST be acyclic (DAG) — circular dependencies are a blocking finding
- Independent units (no edges between them in the DAG) MAY execute in parallel
- Dependent units MUST execute sequentially — the consumer waits for the provider to complete
- The parallelizable fraction `p` SHOULD be estimated: `p = (independent units) / (total units)` — this informs whether parallel execution adds value

**Verification**:
- A dependency graph (textual, Mermaid diagram, or structured list) exists in the workflow plan
- The graph is a valid DAG (no cycles)
- Every unit's inputs are satisfied by either: (a) pre-existing artifacts, or (b) a unit that completes before it starts
- No unit consumes artifacts from a unit running concurrently unless an explicit interface contract is defined
- The estimated parallelizable fraction `p` is documented

---

## Rule MULTI-AGENT-03: Interface Contracts Before Parallel Execution

**Rule**: When multiple units will produce code that must integrate (e.g., one unit provides an API that another consumes), the interface contract MUST be defined and frozen before any parallel unit begins code generation.

Requirements:
- Interface contracts include: function signatures, API schemas (OpenAPI, GraphQL SDL, protobuf), event schemas, shared type definitions, and database schema definitions
- Contracts MUST be documented in a shared location accessible to all agents (e.g., `aidlc-docs/contracts/` or a dedicated interface unit)
- Once frozen, contracts MUST NOT be modified by any parallel unit without halting all dependent units and re-coordinating
- Contract-first development: integration tests against the contract SHOULD be written before implementation begins

**Verification**:
- All cross-unit interfaces are documented as explicit contracts before parallel code generation starts
- Contract documents are placed in a shared location (not within any single unit's owned files)
- No parallel unit modifies a frozen contract without documented coordination and re-approval
- Integration tests or contract tests exist for each defined interface

---

## Rule MULTI-AGENT-04: Isolated Workspace Per Agent

**Rule**: Each parallel agent instance MUST operate in an isolated workspace to prevent file system conflicts. Acceptable isolation strategies include:

| Strategy | When to Use | Trade-offs |
|---|---|---|
| Git worktree per agent | Agents modify different areas of the same repo | Low overhead, native git merge |
| Feature branch per agent | Standard git workflow with PR-based integration | Familiar, good tooling support |
| Subdirectory isolation | Each unit's files are in a non-overlapping directory subtree | Simplest, no git complexity |
| Container/sandbox per agent | Maximum isolation needed (e.g., different runtime dependencies) | Heavy, slower setup |

Requirements:
- The chosen isolation strategy MUST be documented in the workflow plan
- If using git branches or worktrees, the merge strategy MUST be defined (e.g., "rebase onto main after completion", "squash merge via PR")
- Direct writes to a shared branch by multiple agents simultaneously are PROHIBITED
- A merge/integration step MUST be planned after parallel units complete

**Verification**:
- The workflow plan documents which isolation strategy is used
- No two agents write to the same branch or worktree simultaneously
- A merge/integration step is included in the workflow plan after parallel units complete
- Merge conflict resolution responsibility is assigned (specific agent, orchestrator, or human)

---

## Rule MULTI-AGENT-05: Straggler Detection and Timeout Policy

**Rule**: Every parallel unit MUST have a defined timeout — the maximum wall-clock time allowed before the unit is considered a straggler. Straggler handling prevents one slow agent from blocking the entire workflow.

Requirements:
- Each unit MUST declare an estimated completion time and a hard timeout (typically 2x the estimate)
- When a unit exceeds its hard timeout, the orchestrator MUST take action: (a) kill and reassign, (b) kill and skip (if non-critical), or (c) extend with justification logged
- The timeout policy MUST be documented in the workflow plan
- Progress checkpoints (e.g., "design complete", "code generated", "tests passing") SHOULD be defined so stragglers can be detected early, not just at timeout

**Verification**:
- Every parallel unit has a documented estimated completion time and hard timeout
- The workflow plan defines straggler handling policy (kill/reassign, kill/skip, or extend)
- Progress checkpoints are defined for units with estimated duration > 30 minutes
- Timeout actions are logged in `aidlc-docs/audit.md` when triggered

---

## Rule MULTI-AGENT-06: No Shared Mutable State During Parallel Execution

**Rule**: Parallel agents MUST NOT share mutable state during concurrent execution. This includes:
- Shared databases that multiple agents write to simultaneously
- Shared configuration files modified by multiple agents
- Global state files (e.g., `aidlc-docs/aidlc-state.md`) written by multiple agents concurrently
- Shared caches or temporary directories
- Environment variables or secrets stores modified during execution

Requirements:
- Each agent operates on its own state in isolation
- If shared state is required (e.g., a shared database for integration testing), access MUST be serialized — only one agent writes at a time, using a lock or queue mechanism
- Read-only access to shared resources (e.g., reading a frozen contract, reading environment variables) is permitted
- The `aidlc-docs/audit.md` file MUST be written by the orchestrator (not individual parallel agents) or use append-only writes with agent-prefixed entries to prevent conflicts

**Verification**:
- No two parallel agents write to the same mutable resource (file, database, cache) simultaneously
- Shared resources accessed during parallel execution are documented as read-only
- Audit log writes during parallel execution use agent-prefixed entries or are orchestrator-managed
- If a shared database is used for integration testing, write serialization is documented

---

## Rule MULTI-AGENT-07: Integration Verification After Merge

**Rule**: After all parallel units complete and their outputs are merged, a mandatory integration verification step MUST execute. This step validates that independently-developed units work together correctly.

Requirements:
- Integration verification MUST include: (a) successful build of the combined codebase, (b) all unit tests pass, (c) integration tests pass against defined contracts (MULTI-AGENT-03), and (d) no regressions in previously-passing tests
- If integration fails, the failure MUST be attributed to a specific unit and that unit's agent MUST fix the issue before the workflow proceeds
- The integration step MUST NOT be skipped or deferred — it is a hard gate before the "Build and Test" stage completion
- Merge conflicts discovered during integration MUST be resolved and the resolution documented in `aidlc-docs/audit.md`

**Verification**:
- An integration verification step exists in the workflow plan after parallel unit merge
- Integration includes: build success, unit test pass, contract/integration test pass
- Integration failures are attributed to specific units with clear ownership for resolution
- Merge conflict resolutions are documented
- The integration step completion is logged in `aidlc-docs/audit.md`

---

## Rule MULTI-AGENT-08: Parallelism Cost-Benefit Assessment

**Rule**: Before adopting multi-agent parallel execution, the workflow plan MUST include a cost-benefit assessment. Parallelism has overhead (coordination, merge, integration testing) that can exceed the time saved for low-parallelism workloads.

Assessment criteria:
- **Estimate `p` (parallelizable fraction)**: What percentage of units have zero dependencies on each other?
- **Apply Amdahl's bound**: With N agents, maximum speedup is `1 / ((1-p) + p/N)`. If `p < 0.5`, the maximum theoretical speedup with infinite agents is < 2x — coordination overhead likely negates the benefit.
- **Count coordination costs**: Interface contracts, merge steps, integration tests, conflict resolution — each adds wall-clock time
- **Decision threshold**: Multi-agent parallel execution is RECOMMENDED when `p > 0.6` and the number of independent units is ≥ 3. Below this threshold, sequential execution with a single agent is likely faster.

The assessment MUST document:
- Total units, independent units, estimated `p`
- Theoretical maximum speedup for the planned number of agents
- Coordination overhead estimate (contracts, merge, integration)
- Decision: parallel or sequential, with rationale

**Verification**:
- The workflow plan includes a parallelism cost-benefit section
- The parallelizable fraction `p` is estimated and documented
- Amdahl's theoretical speedup is calculated for the planned agent count
- A clear decision (parallel vs sequential) is documented with rationale
- If `p < 0.5` and parallel execution is still chosen, exceptional justification is provided and logged

---

## Rule MULTI-AGENT-09: Communication Protocol

**Rule**: When multiple agents need to communicate during execution (e.g., to signal completion, report failures, or request interface clarifications), a defined communication protocol MUST be established.

Requirements:
- Communication channels MUST be defined (e.g., shared status file, message queue, orchestrator relay, PR comments)
- Message format MUST be structured (not free-form natural language) to prevent misinterpretation
- Agents MUST NOT directly modify each other's workspaces — all cross-agent communication goes through the defined channel
- The orchestrator role MUST be clearly assigned (human, coordinating agent, or CI system)
- Status updates at defined checkpoints (see MULTI-AGENT-05) MUST use the communication protocol

Recommended communication patterns:

| Pattern | Description | When to Use |
|---|---|---|
| Centralized orchestrator | One coordinator dispatches tasks and collects results | Default — simplest, lowest conflict risk |
| Shared status file | Each agent appends status to a shared file (append-only) | Lightweight, asynchronous coordination |
| Event-based | Agents emit events (completion, failure), orchestrator reacts | Complex workflows with dynamic routing |
| PR-based | Each agent opens a PR, CI validates, orchestrator merges | Standard git workflow, human-in-the-loop |

**Verification**:
- A communication protocol is documented in the workflow plan
- Communication channels are defined and accessible to all agents
- Message format is structured (not ad-hoc natural language)
- The orchestrator role is explicitly assigned
- Agents do not directly modify each other's workspaces

---

## Rule MULTI-AGENT-10: Rollback and Recovery Plan

**Rule**: The workflow plan MUST define how to recover from partial failures in multi-agent parallel execution. When one agent fails or produces incorrect output, the system must be able to recover without losing work from successful agents.

Requirements:
- Each unit's work MUST be independently committable (its own branch, worktree, or isolated directory)
- A failed unit MUST be retryable without affecting completed units
- The workflow plan MUST define what happens when: (a) one unit fails and others succeed, (b) integration fails after merge, (c) a straggler is killed
- Rollback granularity: the smallest unit of rollback is one unit's entire output (partial unit rollback is not required)
- Successful units that pass integration MUST NOT be rolled back due to failures in unrelated units

**Verification**:
- Each unit's output is independently revertible (separate branch, commit, or directory)
- The workflow plan documents recovery procedures for: single unit failure, integration failure, straggler timeout
- Successful units are preserved when unrelated units fail
- Retry procedures do not require re-executing successful units
- Recovery actions are logged in `aidlc-docs/audit.md`

---

## Rule MULTI-AGENT-11: Speculative Branching for High-Uncertainty Units (Advisory)

**Rule** *(advisory; never blocking)*: For units that are high-uncertainty *and* sit on the critical path, the orchestrator MAY dispatch N parallel variant attempts gated by Expected Value of Information (EVOI), and select the winner via Pareto front on `(quality, cost, latency)` instead of one-shot-then-blind-retry. This rule is non-blocking even under **Full** enforcement — it is a budgeted optimization, not a safety constraint.

This rule extends MULTI-AGENT-08 (cost-benefit). MULTI-AGENT-08 governs whether to parallelize *across units*; MULTI-AGENT-11 governs whether to parallelize *within a single high-stakes unit*. The two are independent — a project may run units sequentially overall while still forking a single critical unit speculatively.

When this rule applies:
- A unit's recorded historical failure rate is high (≥ 0.5) OR
- A unit has sparse acceptance criteria (≤ 1 explicit criterion) AND is on the critical path OR
- A unit's combined uncertainty score (failure history + complexity + criticality + ambiguity) crosses a documented `min_uncertainty` floor

When the EVOI gate fires:
- Dispatch N branches (typically 3) with explicit variant labels (e.g., `conservative`, `balanced`, `aggressive`) — each branch gets a distinct rewrite preamble
- Each branch operates in an isolated workspace (MULTI-AGENT-04 still applies)
- Branches share no mutable state (MULTI-AGENT-06 still applies)
- After all branches complete, select the winner via Pareto front with min-quality and per-branch budget cap; record every branch's outcome (winner *and* losers) so future signals reflect both successful and failed alternatives

EVOI formula (deterministic, no LLM call):

```
EVOI = uncertainty_total · expected_improvement_usd − branch_cost_total
fork ⇔ EVOI > 0  AND  uncertainty_total ≥ min_uncertainty  AND  budget_remaining ≥ branch_cost_total
```

**Recommended Verification** *(advisory)*:
- The orchestrator documents which units crossed the EVOI threshold and the gate decision (fork or skip) per unit, with the recorded reason
- Forked units use isolated workspaces (one per branch) and do not violate MULTI-AGENT-01/04/06
- Every branch outcome — winner *and* losers — is logged in `aidlc-docs/audit.md` with quality, cost, and duration; the winner is committed and losers are dropped (rollback granularity: one branch)
- Branch cost is bounded — total speculative spend per unit is capped (e.g., `n_branches × per_branch_cost ≤ unit_budget`); breaches are logged
- A reference implementation is available in `scripts/multi-agent-validator/speculative_branching.py`; projects MAY adopt it or implement their own

This rule is a **strict opt-in** beyond the standard extension opt-in: the orchestrator (human or coordinating agent) must explicitly enable it for a project. The default behavior even under **Full** enforcement is single-agent-per-unit dispatch.

---

## Enforcement Integration

These rules are cross-cutting constraints that apply to the following AI-DLC stages:

| Stage | Applicable Rules | Enforcement |
|---|---|---|
| Workflow Planning | MULTI-AGENT-01, 02, 03, 04, 05, 08, 09, 10, 11 | Parallel execution plan must include ownership, DAG, contracts, isolation, timeouts, cost-benefit, communication, recovery, and (advisory) speculative-branching policy |
| Units Generation | MULTI-AGENT-01, 02 | Each generated unit must have declared file ownership and dependency edges |
| Functional Design | MULTI-AGENT-03 | Cross-unit interface contracts must be defined and frozen before parallel design begins |
| Code Generation | MULTI-AGENT-04, 06 | Each agent operates in isolated workspace with no shared mutable state |
| Build and Test | MULTI-AGENT-05, 07 | Straggler detection active; integration verification mandatory after merge |

At each applicable stage:
- Evaluate all MULTI-AGENT rule verification criteria against the artifacts produced
- Include a "Multi-Agent Compliance" section in the stage completion summary listing each rule as compliant, non-compliant, or N/A
- If any rule is non-compliant, this is a blocking multi-agent finding — follow the blocking finding behavior defined in the Overview
- Include MULTI-AGENT rule references in planning documentation and coordination artifacts

---

## Appendix A: Amdahl's Law Quick Reference

For orchestrators estimating parallelism benefit:

| Parallelizable Fraction (p) | Max Speedup (∞ agents) | Practical Speedup (4 agents) | Recommendation |
|---|---|---|---|
| > 90% | ~10x | ~3.1x | Strong parallel — use 4-8 agents |
| 70-90% | 3.3-10x | ~2.4-3.1x | Parallel with care — use 3-4 agents |
| 50-70% | 2-3.3x | ~1.8-2.4x | Marginal — use 2-3 agents, monitor overhead |
| 30-50% | 1.4-2x | ~1.4-1.8x | Likely not worth it — single agent preferred |
| < 30% | < 1.4x | < 1.4x | Sequential wins — do not parallelize |

**Formula**: `Speedup(N) = 1 / ((1-p) + p/N)`

---

## Appendix B: Common Anti-Patterns

| Anti-Pattern | Symptom | Root Cause | Prevention Rule |
|---|---|---|---|
| Write conflict | Two agents modify the same file, merge fails | No file ownership declaration | MULTI-AGENT-01 |
| Silent rewrite | Agent B overwrites Agent A's code without conflict (e.g., same branch) | No workspace isolation | MULTI-AGENT-04 |
| Dependency race | Agent starts before its input is ready, produces incorrect output | No dependency graph | MULTI-AGENT-02 |
| Interface drift | Agents implement incompatible interfaces that fail at integration | No frozen contracts | MULTI-AGENT-03 |
| Straggler bottleneck | One slow agent blocks all others from integration | No timeout policy | MULTI-AGENT-05 |
| State corruption | Two agents write to shared database, producing inconsistent state | Shared mutable state | MULTI-AGENT-06 |
| Integration surprise | Independently-correct units fail when combined | No integration verification | MULTI-AGENT-07 |
| Negative returns | Parallelism overhead exceeds sequential time | No cost-benefit assessment | MULTI-AGENT-08 |
| Miscommunication | Agents duplicate work or miss signals | No communication protocol | MULTI-AGENT-09 |
| Cascading rollback | One failure destroys all parallel work | No rollback plan | MULTI-AGENT-10 |

---

## Appendix C: Integration with Orchestration Tools

These rules are tool-agnostic but map cleanly to common orchestration approaches:

| Orchestration Approach | How Rules Apply |
|---|---|
| **Manual (human coordinator)** | Human creates ownership matrix, dispatches to agents, monitors progress, triggers integration |
| **CI/CD pipeline (GitHub Actions, CodeBuild)** | Jobs map to units, job dependencies encode the DAG, artifacts pass between jobs, integration is a final job |
| **Agent framework (Strands, LangGraph, CrewAI)** | Units map to agent tasks, framework manages state isolation, coordinator agent enforces rules |
| **IDE multi-agent (Kiro multi-file, Cursor Composer)** | IDE manages file scope per suggestion, user acts as orchestrator for conflict resolution |

These rules do not prescribe a specific tool — they prescribe the constraints any tool must satisfy for safe parallel agent execution.
