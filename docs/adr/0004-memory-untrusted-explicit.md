# ADR 0004: Durable Memory Is Explicit, Scoped, and Untrusted

- Status: Accepted
- Date: 2026-08-21

## Context

Automatically storing model-generated conclusions risks preserving hallucinations, sensitive data,
stale facts, and prompt injection. A unified assistant/story platform also risks cross-contaminating
campaign fiction and real infrastructure knowledge.

## Decision

Phase-one durable memory is created explicitly through an API, stored in PostgreSQL, and filtered
by user and namespace. Retrieval uses full-text search first; a vector column is reserved for later
hybrid retrieval. Retrieved records are injected as untrusted reference data and never as
instructions.

The assistant defaults to `user`, `infrastructure`, `projects`, and `general`; the storyteller
defaults to `story` and `campaign`.

## Consequences

- Memories remain auditable and predictable.
- Important facts require an explicit write or later proposal/review workflow.
- Full-text search is less semantic than embeddings but does not consume T4 capacity or introduce
  another model dependency.
- Future automatic memory extraction must enter a pending-review state rather than writing directly.
