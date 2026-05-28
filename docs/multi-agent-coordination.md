# Multi-Agent Coordination Extension

## Overview

The Multi-Agent Coordination extension provides 10 blocking rules that govern safe parallel execution when multiple AI coding agents work on AI-DLC units concurrently. It prevents empirically-documented failure modes: write conflicts, dependency races, interface drift, straggler bottlenecks, and silent rewrites.

## When to Use

Use this extension when:

- Multiple AI coding agents (or agent instances) work on different units simultaneously
- A coordinating agent dispatches sub-tasks to worker agents
- CI/CD pipelines execute multiple AI-DLC units as parallel jobs
- Team members each run their own AI agent on different parts of the same project

Do **not** enable this extension for:

- Single-agent development (default AI-DLC workflow)
- Sequential unit execution (one unit at a time)
- Pair-programming style human+agent collaboration (one agent, one human)

## Theoretical Foundation

### Amdahl's Law for Agent Teams

The maximum speedup from N parallel agents is bounded by:

```
Speedup(N) = 1 / ((1-p) + p/N)
```

Where `p` is the parallelizable fraction of work (units with zero inter-dependencies / total units).

```
           Speedup
    10x ┤                         ╭── p=1.0 (ideal)
        │                    ╭────╯
     8x ┤               ╭───╯
        │          ╭────╯
     6x ┤     ╭───╯                  ╭── p=0.9
        │╭───╯                  ╭────╯
     4x ┤│              ╭──────╯
        ││         ╭───╯            ╭── p=0.7
     2x ┤│    ╭───╯           ╭────╯
        ││───╯          ╭────╯       ── p=0.5
     1x ┤─────────────────────────────── p=0.3
        └┬────┬────┬────┬────┬────┬──
         1    2    4    8    16   32
                  Agents (N)
```

**Key insight**: When `p < 0.5`, adding agents barely helps. Coordination overhead (merging, conflict resolution, contract maintenance) often makes it *worse* than sequential execution.

### Empirical Anti-Patterns

Research on multi-agent software delivery (arXiv:2603.12229) identifies three primary coordination failure modes:

| Anti-Pattern | Cause | Frequency | Rule Prevention |
|---|---|---|---|
| **Write Conflicts** | Two agents modify same file | High | MULTI-AGENT-01 (File Ownership) |
| **Silent Rewrites** | Agent B overwrites Agent A without conflict detection | Medium | MULTI-AGENT-04 (Isolation) |
| **Dependency Races** | Agent starts before prerequisite completes | High | MULTI-AGENT-02 (DAG) |

## Architecture

### Extension Loading Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. WORKFLOW START                                            │
│    core-workflow.md scans extensions/ directory              │
│    Loads: multi-agent-coordination.opt-in.md (lightweight)  │
│    Does NOT load: multi-agent-coordination.md (full rules)  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. REQUIREMENTS ANALYSIS                                     │
│    Opt-in question presented to user:                        │
│    A) Full enforcement (all 10 rules)                        │
│    B) Partial (rules 01, 02, 03 only)                        │
│    C) Disabled                                               │
└────────────────────────┬────────────────────────────────────┘
                         │ User selects A or B
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. RULE LOADING                                              │
│    NOW loads: multi-agent-coordination.md (full 314 lines)   │
│    Logs enforcement mode in aidlc-docs/aidlc-state.md        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. ENFORCEMENT (per stage)                                   │
│    At each applicable stage:                                 │
│    - Evaluate rule verification criteria                      │
│    - Block if non-compliant                                  │
│    - Include "Multi-Agent Compliance" in completion summary   │
└─────────────────────────────────────────────────────────────┘
```

### Rule-to-Stage Mapping

```
INCEPTION                    CONSTRUCTION              POST-MERGE
─────────────────────────    ──────────────────────    ─────────────
                                                      
Workflow Planning             Code Generation           Build & Test
├─ 01 File Ownership         ├─ 04 Isolation          ├─ 05 Straggler
├─ 02 Dependency DAG         └─ 06 No Shared State    └─ 07 Integration
├─ 03 Interface Contracts                                Verification
├─ 04 Isolation Strategy     
├─ 05 Timeout Policy         
├─ 08 Cost-Benefit           
├─ 09 Communication          
└─ 10 Rollback Plan          
                             
Units Generation             Functional Design
├─ 01 File Ownership         └─ 03 Contracts Frozen
└─ 02 Dependencies           
```

### Compliance Verification Flow (Per Stage)

```
┌─────────────────────┐
│ Stage Work Complete │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────┐
│ For each applicable rule:   │
│ ├─ Check verification items │
│ ├─ Status: ✓ / ✗ / N/A     │
│ └─ If ✗ → blocking finding  │
└──────────┬──────────────────┘
           │
     ┌─────┴─────┐
     │ Any ✗?    │
     └─┬───────┬─┘
       │Yes    │No
       ▼       ▼
┌──────────┐ ┌──────────────────────┐
│ BLOCKED  │ │ Present completion   │
│ Show:    │ │ with compliance      │
│ "Request │ │ summary + "Continue" │
│ Changes" │ └──────────────────────┘
└──────────┘
```

## Example: Compliant Multi-Agent Plan

See `examples/multi-agent-plan/` for a complete example of what compliant artifacts look like when this extension is enabled.

## Relationship to Other Extensions

| Extension | Relationship |
|---|---|
| **Security Baseline** | Complementary — security rules apply within each unit independently |
| **Property-Based Testing** | Complementary — PBT applies per-unit; MULTI-AGENT-07 adds cross-unit integration verification |

## References

- [Amdahl's Law for Agent Teams](https://arxiv.org/abs/2603.12229) — Princeton, MIT, Cambridge, NYU (March 2026)
- [AI-DLC Method Definition Paper](https://prod.d13rzhkk8cj2z0.amplifyapp.com/)
- [AI-DLC Blog Post](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/)
