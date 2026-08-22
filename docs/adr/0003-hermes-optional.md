# ADR 0003: Hermes Agent Is Optional, Not the Baseline Path

- Status: Accepted
- Date: 2026-08-21

## Context

Hermes offers memory, skills, tools, MCP, terminal workflows, and an OpenAI-compatible API server.
Those capabilities are useful for a local operator agent but increase context use, configuration,
privilege, and attack surface. General chat and storytelling should remain usable when Hermes is
unavailable or disabled.

## Decision

Deploy Hermes behind a Compose profile named `agent`. It uses the dedicated
`hermes-agent` backend profile and has its own API key/state. Open WebUI may connect to Hermes
as a second provider, but ordinary assistant and story requests go directly to the Local AI Hub
gateway.

## Consequences

- Baseline reliability is independent of agent tooling.
- Tools can be hardened and approved incrementally.
- Hermes memory/skills may overlap with gateway memory; ownership rules must prevent divergence.
- Agent workflows incur a larger context and may require more CPU offload or a reduced context on
  the T4.
