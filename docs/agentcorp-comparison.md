# AgentCorp vs AI-DLC Multi-Agent Extension: Architecture Comparison

## Executive Summary

This document provides a critical self-assessment comparing AgentCorp (a runtime multi-agent simulation system) with the AI-DLC Multi-Agent Coordination Extension (a methodology/governance layer). It identifies gaps in the extension, features that only AgentCorp provides, and areas where the extension could be strengthened by borrowing AgentCorp patterns.

## Architecture Comparison

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FULL STACK VIEW                                 │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Layer 4: EXECUTION ENGINE           [AgentCorp ONLY]            │   │
│  │  • 12 Strands agents actually running on Bedrock                 │   │
│  │  • Real LLM calls, real code generation, real artifacts          │   │
│  │  • Sprint simulation (10-day cycles, daily velocity)             │   │
│  │  • AgentFactory: "agents-as-tools" pattern                       │   │
│  │  • VelocityTracker: tokens, cost, story points per day           │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Layer 3: GOVERNANCE & GATES         [BOTH — different depths]   │   │
│  │                                                                   │   │
│  │  AgentCorp:                    AI-DLC Extension:                  │   │
│  │  • GovernanceHarness class     • MULTI-AGENT-07 (integration     │   │
│  │  • Input/output gates            verification gate)               │   │
│  │  • Risk classification         • Blocking finding behavior        │   │
│  │  • Human review triggers       • Stage completion gates           │   │
│  │  • AuditRecord (structured)   • audit.md (text-based)            │   │
│  │  • PhaseGate (code enforced)  • Rules (LLM enforced)             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Layer 2: COMMUNICATION PROTOCOL     [BOTH — different models]   │   │
│  │                                                                   │   │
│  │  AgentCorp:                    AI-DLC Extension:                  │   │
│  │  • CommunicationBus class     • MULTI-AGENT-09 (protocol spec)   │   │
│  │  • AgentMessage dataclass     • Structured message format req    │   │
│  │  • MessageType enum (9 types) • Pattern recommendations          │   │
│  │  • Thread tracking            • No runtime implementation        │   │
│  │  • ProtocolViolationError     • No enforcement mechanism         │   │
│  │  • EventBus (pub/sub)         • [NOT PROVIDED]                   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Layer 1: PLANNING & COORDINATION    [AI-DLC Extension primary]  │   │
│  │                                                                   │   │
│  │  AgentCorp:                    AI-DLC Extension:                  │   │
│  │  • team-12.yaml (config)      • MULTI-AGENT-01 (ownership)       │   │
│  │  • Role boundaries            • MULTI-AGENT-02 (DAG)             │   │
│  │  • reports_to hierarchy       • MULTI-AGENT-03 (contracts)       │   │
│  │  • Sprint planning (implicit) • MULTI-AGENT-08 (cost-benefit)    │   │
│  │  • [No Amdahl's estimate]     • MULTI-AGENT-04 (isolation)       │   │
│  │  • [No explicit DAG]          • MULTI-AGENT-05 (timeouts)        │   │
│  │  • [No file ownership matrix] • MULTI-AGENT-10 (rollback)        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Critical Gap Analysis

### What AgentCorp Has That the Extension LACKS

| AgentCorp Feature | Extension Gap | Impact | Recommendation |
|---|---|---|---|
| **GovernanceHarness** (runtime) | Extension only describes rules; no enforcement code | Rules depend on LLM self-compliance (brittle) | Add validator tool (done) + recommend governance harness pattern |
| **CommunicationBus** with structured messages | Extension specifies "use structured messages" but provides no schema | Each project reinvents message format | Add reference message schema |
| **EventBus** (pub/sub within sprint) | No event system concept | Can't track real-time coordination | Add event schema recommendation |
| **VelocityTracker** (metrics) | No cost/token tracking in rules | Can't validate MULTI-AGENT-08 empirically | Add metrics collection recommendation |
| **Risk Classification** (LOW/MEDIUM/HIGH/PROHIBITED) | No risk-based gate escalation | All blocking findings treated equally | Add risk tiers to gate behavior |
| **AgentFactory** ("agents-as-tools") | No guidance on agent instantiation | Extension is framework-agnostic but too abstract | Add implementation patterns appendix |
| **PhaseGate** (code-enforced transitions) | Rules say "must pass" but have no code gate | Relies on LLM reading rules correctly | Validator partially addresses this |
| **Sprint Simulation** (10-day cycles) | No time-boxing concept beyond timeouts | No velocity-based adaptation | Out of scope (methodology vs runtime) |
| **12-agent team configuration** | No team composition guidance | Extension says "multiple agents" but not how many | Add sizing recommendations |
| **ProtocolViolationError** (runtime exception) | No violation detection mechanism | Protocol violations go undetected | Validator partially addresses this |

### What the Extension Has That AgentCorp LACKS

| Extension Rule | AgentCorp Gap | Why It Matters |
|---|---|---|
| **MULTI-AGENT-01** (File Ownership) | AgentCorp uses "boundaries" text but no file-level lock | S1 experiment showed Tech Lead doing everything alone — no ownership enforcement |
| **MULTI-AGENT-02** (Explicit DAG) | AgentCorp uses `reports_to` hierarchy, not task DAG | Hierarchy ≠ dependency graph; agents can still race |
| **MULTI-AGENT-08** (Amdahl's Cost-Benefit) | No theoretical assessment before parallelizing | S1 result: delegation overhead > benefit for simple tasks |
| **MULTI-AGENT-04** (Workspace Isolation) | All agents write to same `output_dir` | Potential file conflicts in concurrent phases |
| **MULTI-AGENT-10** (Rollback Plan) | No explicit rollback mechanism | If agent fails mid-sprint, manual intervention needed |
| **MULTI-AGENT-05** (Straggler Timeout) | No per-agent timeout | Slow agent blocks entire sprint day |

## Key Insight: AgentCorp S1 Result

> "Tech Lead solved everything alone — delegation overhead wasn't worth it for simple tasks"

This validates **MULTI-AGENT-08** (cost-benefit assessment). In the S1 scenario (5 agents, simple task), `p` was effectively < 0.3. The overhead of routing work through 5 agents produced negative returns. A cost-benefit gate would have recommended single-agent execution.

## Verdict: Complementary, Not Competing

| Dimension | AgentCorp | AI-DLC Extension |
|---|---|---|
| **Nature** | Runtime system (code runs) | Methodology (rules guide) |
| **Enforcement** | Code-enforced (Python classes) | LLM-interpreted (markdown rules) |
| **Audience** | Developers building agent systems | Developers using AI coding assistants |
| **Flexibility** | Fixed architecture (12 agents, YAML config) | Any number of agents, any tool |
| **Governance depth** | Deep (harness, gates, risk, audit) | Broad (10 rules, blocking findings) |
| **Empirical data** | Yes (sprint results, velocity, cost) | Theoretical (Amdahl's, pattern catalog) |
| **Reusability** | Fork the repo, customize YAML | Copy rules into any AI-DLC project |

**The extension is the "building code"; AgentCorp is a "building that passes inspection."**
