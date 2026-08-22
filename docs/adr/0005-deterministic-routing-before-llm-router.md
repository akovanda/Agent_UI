# ADR 0005: Deterministic Routing Before an LLM Router

- Status: Accepted
- Date: 2026-08-21

## Context

An automatic model selector is desirable, but using a third model or an additional LLM call would
increase latency, consume resources, and make decisions difficult to reproduce. Routing mistakes
are particularly expensive because they trigger a model swap.

## Decision

Start with explicit model selection, control prefixes, headers, and a deterministic scored
classifier for `auto`. Expose a route-preview endpoint and route-reason response header.
Collect an evaluation set before considering a learned classifier.

## Consequences

- Decisions are fast, explainable, and testable.
- Ambiguous prompts may route conservatively to the general assistant.
- Clients can always override the result.
- A future classifier must outperform the deterministic baseline on a versioned evaluation set.
