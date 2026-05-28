# Multi-Agent Coordination — Opt-In

**Extension**: Multi-Agent Coordination

## Opt-In Prompt

The following question is automatically included in the Requirements Analysis clarifying questions when this extension is loaded:

```markdown
## Question: Multi-Agent Coordination Extension
Will this project use multiple AI coding agents working in parallel (e.g., parallel units assigned to separate agents, multi-agent orchestration, or team-of-agents workflows)?

A) Yes — enforce all MULTI-AGENT rules as blocking constraints (recommended when units are developed concurrently by multiple agents or agent instances)

B) Partial — enforce file ownership and dependency rules only (suitable for projects with limited parallelism or manual coordination between agents)

C) No — skip all MULTI-AGENT rules (suitable for single-agent workflows or fully sequential development)

X) Other (please describe after [Answer]: tag below)

[Answer]: 
```
