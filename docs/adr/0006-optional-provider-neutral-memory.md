# ADR 0006: Optional Provider-Neutral Memory

- Status: Accepted
- Date: 2026-08-24

## Context

The original gateway stored records directly in one PostgreSQL table and accepted caller-selected
user and namespace headers. It had no review queue, correction/forget/purge lifecycle, independent
space ACL, provider contract, or safe boundary between personal and game continuity. A richer
private continuity system exists, but making it a public dependency or a hard-coded installation
path would make Agent UI less generic and force unrelated users to adopt it.

## Decision

Agent UI owns a small, optional memory policy layer and a generic provider SPI.

- Automatic behavior is off until explicit operator enablement; enabled installations default
  accounts on with per-account opt-out.
- Built-in PostgreSQL remains the public fallback and compatibility provider.
- External implementations use the discoverable `continuity-http/1` contract.
- Trusted signed identity maps to server-owned spaces and memberships. Provider subjects and chat
  sessions are HMAC-pseudonymous.
- Personal memory is shared by eligible chat/code/agent experiences and excluded from story/game
  scopes by default.
- Post-response extraction uses only the latest user-authored text and creates inactive proposals.
  Approval or edit is required before provider ingestion.
- Agent UI owns settings, spaces, memberships, proposals, provider references, bridge consent,
  jobs, and content-free lifecycle audit. Providers own memory content and retrieval projections.
- Correction, soft forget, hard purge, and explicit export are mandatory for personal providers.
- Bridges are directional read queries requiring an operator kind-pair allowlist and exact
  source/target user consent. They never cross-write.
- A service-authenticated game seam models namespace → world → campaign → player → session without
  bundling a game implementation.

## Consequences

Private providers can be selected in ignored installation state without appearing in public source
or model catalogs. Provider outages cannot silently create split memory because Agent UI continues
without optional memory or fails when the provider is required. The built-in implementation has
more control-plane tables, and operators must back up both the provider and Agent UI metadata.

The Memory page is intentionally smaller than a full chat UI. Open WebUI remains the primary human
surface and supplies signed identity; other clients must use fixed-principal keys or an equivalent
trusted integration.
