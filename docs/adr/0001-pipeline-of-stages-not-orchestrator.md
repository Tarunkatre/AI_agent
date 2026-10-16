# Pipeline of specialized stages with localized judgment, not an orchestrator of agents

## Status

accepted

## Context

The platform is described as "agentic," but the reliability requirements differ sharply per sub-problem: FX translation is arithmetic, COA mapping is mostly deterministic matching with a genuine judgment residual, and adjustment review needs narration. An LLM planner in the control loop would add nondeterminism and cost to steps that must replay identically and be auditable.

## Decision

The system is an ordered DAG (ingest → translate → map → adjust → eliminate → build statements → validate), where each stage is deterministic code with, at most, one bounded LLM sub-step at its judgment point. A single validation/audit spine runs alongside the DAG and every stage exits through the same Validation Gate. No planner LLM, no dynamic tool selection, no agent-to-agent messaging.

## Considered Options

- **Single agent with tools** — rejected: puts an unbounded reasoning loop around arithmetic that must be exactly reproducible.
- **Orchestrator + sub-agents (Mapper, Adjuster, Builder, Validator)** — rejected as day-one architecture: recreates nondeterminism at every hop and is the "12-agent swarm for a 200-line script" failure mode the assignment warns against. Sub-agents may be introduced later as bounded stages behind the same Validation Gate, where narration (e.g. adjustment rejection explanations) earns their keep.

## Consequences

- Idempotency and traceability are properties of the deterministic stages; only the LLM sub-steps carry practical (not guaranteed) determinism, and they are confined behind schema-constrained contracts.
- Each stage can declare its own reliability bar (e.g. mapping blocks on ambiguity, FX never blocks) without a global planner mediating.
