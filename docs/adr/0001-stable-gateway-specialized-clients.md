# ADR 0001: Stable Gateway with Specialized Clients

- Status: Accepted
- Date: 2026-08-21

## Context

Open WebUI is strong for everyday assistant interaction, while SillyTavern is purpose-built for
characters, lorebooks, campaigns, and roleplay context. Hermes adds agent tooling but introduces a
larger prompt, more privileges, and a different failure mode. Coupling model lifecycle and memory
to any one UI would make the platform difficult to replace or debug.

## Decision

Use a small OpenAI-compatible gateway as the stable control/data plane. Open WebUI and SillyTavern
are specialized clients of that gateway. Hermes is a separate optional endpoint that uses the same
gateway for inference.

The gateway owns profile resolution, shared memory retrieval, one-GPU leasing, model lifecycle,
streaming proxying, and metrics. It does not own UI-specific character/lore state or execute tools.

## Consequences

- UIs and inference engines can change independently.
- Model switching is testable without a browser.
- Shared memory can span clients while remaining namespace-scoped.
- There is one additional service and API compatibility layer to maintain.
- Full OpenAI API parity is explicitly out of scope; only required endpoints are implemented.
