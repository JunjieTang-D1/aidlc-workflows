# Speculative Branching for Multi-Agent Coordination

**Rule reference:** [MULTI-AGENT-11](../aidlc-rules/aws-aidlc-rule-details/extensions/agentic/multi-agent-coordination/multi-agent-coordination.md#rule-multi-agent-11-speculative-branching-for-high-uncertainty-units-advisory) (advisory; never blocking).
**Reference implementation:** `scripts/multi-agent-validator/speculative_branching.py`.
**Tests:** `scripts/multi-agent-validator/tests/test_speculative_branching.py` (31 cases).

## Why this exists

The base coordination harness (`multi_agent_harness.py`) is V12-equivalent: one agent per unit, observe one trajectory, block on conflicts. That works when every unit is well-specified.

It does **not** work for units that

- historically fail (e.g. `replay-runner`, `governance-modes` in dealscope),
- have ambiguous acceptance criteria that admit several reasonable rewrites,
- sit on the critical path so a retry costs the whole sprint's wall clock.

For those units the orchestrator should spend a bit more budget up-front to **explore alternatives** instead of one-shot-then-blind-retry. That is exactly what AgentCorp V13 does at the TPG-node level. This module brings the same idea to AI-DLC unit dispatch.

Two design constraints, taken from V13:

1. **No LLM dependency.** Uncertainty + EVOI are deterministic functions of recorded history and plan metadata. No new model dependency; runs are reproducible.
2. **Opt-in, advisory.** The fork recommendation is wired through MULTI-AGENT-11 (advisory). Even under **Full** enforcement of the multi-agent extension, this rule is non-blocking. Cost-benefit stays the operator's call.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1.  Plan dispatch                                                    │
│     UnitDescriptor + UnitHistoryStore                                │
│     ├─► UnitUncertaintyDetector.signal()                             │
│     │     historical_failure_rate · 0.30                             │
│     │   + complexity                · 0.25                           │
│     │   + criticality               · 0.30                           │
│     │   + ambiguity                 · 0.15  → total ∈ [0, 1]         │
│     └─► EVOIGate.decide(signal, budget_remaining_usd)                │
│           EVOI = total · expected_improvement_usd − branch_cost_total│
│           fork ⇔ total ≥ floor  AND  budget ≥ cost  AND  EVOI > 0    │
│                                                                      │
│         fork=False                fork=True                          │
│         single dispatch           N branches with distinct preambles │
│                                                                      │
│ 2.  Run branches                                                     │
│     Orchestrator (Strands / GitHub Actions / IDE composer / human)   │
│     executes each branch in its own workspace + git branch           │
│     (MULTI-AGENT-04 isolation still applies)                         │
│                                                                      │
│ 3.  Pareto-select + record                                           │
│     SpeculativeDispatcher.record_outcomes(unit, [BranchResult, ...]) │
│     ├─► BranchEvaluator.select():                                    │
│     │     drop crashed → drop below min_quality → drop over cap →    │
│     │     Pareto front on (quality↑, cost↓, latency↓) →              │
│     │     composite tiebreaker → winner + losers                     │
│     └─► UnitHistoryStore.record() for ALL branches                   │
│         (winner *and* losers — that's the contrastive-learning hook) │
└─────────────────────────────────────────────────────────────────────┘
```

## The EVOI formula

```
EVOI = uncertainty_total · expected_improvement_usd − branch_cost_total
```

`expected_improvement_usd = criticality · improvement_scale_usd` — the value of getting a *better* answer scales with how critical the unit is. `improvement_scale_usd` is a single, traceable knob (default `$5`). The gate is conservative: any of (uncertainty < floor, budget headroom < cost, EVOI ≤ 0) returns `fork=False` with the reason filled in.

## Pareto front: why three axes

V13 picks winners on `(completion, quality, cost_score)`. Speculative branching uses `(quality, cost, latency)` because at the AI-DLC unit level wall-clock matters: a high-quality branch that took 2× the estimate still costs the sprint critical-path. The dispatcher drops crashed and below-floor branches first, then builds the front, then tie-breaks with a composite score normalized within the branch set so absolute USD / minute don't distort the comparison.

## Worked example: dealscope DS-009 (governance modes)

DS-009 in `code.aws.dev/agentcorp-usecases/dealscope/domains/dealscope/STORIES.md` is a 5-point story with four selectable governance modes (`baseline`, `cross_validation`, `confidence_gated`, `adversarial`). It has historically been one of the harder stories — sparse criteria for what counts as a passing `confidence_gated` implementation, and any failure cascades into Phase 4 synthesis (the critical path).

### Planning the dispatch

```python
from pathlib import Path
from speculative_branching import (
    SpeculativeDispatcher, UnitHistoryStore, UnitDescriptor, EVOIGate,
)

history = UnitHistoryStore(Path("aidlc-docs/.speculative-history.json"))
dispatcher = SpeculativeDispatcher(
    history,
    gate=EVOIGate(n_branches=3, per_branch_cost_usd=1.5,
                  min_uncertainty=0.35, improvement_scale_usd=10.0),
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
```

If the unit has no recorded history yet, the prior puts `historical_failure_rate ≈ 0.4`. With 2 deps, 2 owned-file globs, a 180-min estimate, AC=4, and `on_critical_path=True`, the components are roughly `complexity ≈ 0.28`, `criticality = 1.0`, `ambiguity = 0.4`. Total = `0.30·0.4 + 0.25·0.28 + 0.30·1.0 + 0.15·0.4 ≈ 0.55` — above the `0.35` floor. The gate computes `EVOI = 0.55 · $10.00 − 3 · $1.50 = $1.00 > 0` and returns `fork=True`.

The dispatcher hands back three branches:

| branch_id | variant | suggested branch | workspace |
|---|---|---|---|
| `governance-modes::b0` | conservative | `unit/governance-modes/b0-conservative` | `.worktrees/governance-modes/b0-conservative` |
| `governance-modes::b1` | balanced | `unit/governance-modes/b1-balanced` | `.worktrees/governance-modes/b1-balanced` |
| `governance-modes::b2` | aggressive | `unit/governance-modes/b2-aggressive` | `.worktrees/governance-modes/b2-aggressive` |

Each branch's `instruction_preamble` differs — conservative reuses existing patterns, aggressive may restructure the unit's owned files. The orchestrator (whatever runtime is in use) dispatches each branch in isolation; MULTI-AGENT-04 (workspace isolation) and MULTI-AGENT-06 (no shared mutable state) still apply.

### Recording outcomes

After all three branches finish:

```python
from speculative_branching import BranchResult

results = [
    BranchResult("governance-modes::b0", "governance-modes",
                 quality_score=0.78, cost_usd=1.35, duration_minutes=42),
    BranchResult("governance-modes::b1", "governance-modes",
                 quality_score=0.92, cost_usd=1.55, duration_minutes=51),
    BranchResult("governance-modes::b2", "governance-modes",
                 quality_score=0.40, cost_usd=2.10, duration_minutes=68,
                 error="confidence_gated test fails"),
]
selection = dispatcher.record_outcomes("governance-modes", results)
print(selection.winner.branch_id)         # governance-modes::b1
print(selection.rationale)
```

What gets recorded:
- `b0` → `SUCCESS` (quality 0.78 ≥ 0.5, no error)
- `b1` → `SUCCESS` (winner)
- `b2` → `FAILURE` (error set; failure reason captured for the next signal)

All three are written to `UnitHistoryStore`, so the next sprint that touches `governance-modes` sees three contributing attempts (one win, one ok, one fail), not just the winner. That's the contrastive-learning hook.

### Cost discipline

In this example the speculative spend is $5.00 (3 × ≈$1.55). Without branching the team would have done one attempt at $1.55, and on the historical 40% failure rate paid for a full retry — $3.10 in expectation, plus a day of wall clock. EVOI says: pay $5 now, get the winner deterministically.

When EVOI does **not** clear (cheap unit, well-specified, peripheral), the gate returns `fork=False` and the dispatcher returns a single-branch plan. The base harness handles the unit normally.

## What this does not do

Honest scope statement:

- **Not a runtime.** The dispatcher returns a plan; the orchestrator runs branches. Plug it into Strands, LangGraph, GitHub Actions matrix jobs, IDE composer multi-tab, or a shell script — any runtime that supports parallel isolated workspaces.
- **Not a quality scorer.** Caller supplies `BranchResult.quality_score`. Use whatever signal makes sense in your project — tests-passing rate, judge LLM score, replay-benchmark equality, etc.
- **Not a guarantee.** The Pareto front depends entirely on the quality signal you feed in. Garbage in → garbage out.
- **Not a replacement for MULTI-AGENT-08.** That rule governs whether to parallelize *across units*; this one governs whether to fork *within a single unit*. They are independent — running units sequentially overall is still compatible with forking one critical unit speculatively.

## When to enable

| Situation | Recommendation |
|---|---|
| Project has a small number of high-stakes units (judge gate, replay benchmark, governance modes, contract-test runners) | Strong yes |
| All units are simple CRUD, well-specified | No — single-agent dispatch wins on cost |
| Budget is tight and `improvement_scale_usd` is small | No — EVOI rarely clears |
| You don't have a usable per-unit quality signal | Not yet — invest in the signal first |

## Relationship to V13

| V13 (TPGO) | Speculative Branching (this) |
|---|---|
| Forks at TPG decision nodes (intra-iteration) | Forks at AI-DLC unit dispatch (intra-sprint) |
| Uncertainty: gradient variance + semantic distance + historical failure + economic impact | Uncertainty: historical failure + complexity + criticality + ambiguity |
| EVOI gate + Pareto on (completion, quality, cost_score) | EVOI gate + Pareto on (quality, cost, latency) |
| Contrastive gradients fed back to GRAO | All branch outcomes (winner + losers) appended to UnitHistoryStore |
| Bedrock/SDK runtime (executes branches itself) | Runtime-agnostic (returns a plan; caller runs branches) |
| Engineered for one TPG per use-case pack | Engineered to compose with the existing multi-agent extension |

The structural similarities are intentional. The differences are deliberate: the AI-DLC layer doesn't own the runtime, doesn't have a single graph, and has to be cheap enough to enable on every project — not just benchmark runs.
