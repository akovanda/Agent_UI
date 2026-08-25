# Optional Memory Foundation

Agent UI memory is an optional, provider-neutral layer. The public project ships with the built-in
PostgreSQL provider configured, but automatic retrieval and capture are off. An operator can use a
different implementation without changing models, experiences, or public source.

```text
signed user identity
        │
        ▼
Agent UI policy and spaces ──► provider SPI ──► built-in PostgreSQL
        │                           └──────────► continuity-http/1
        ├── settings
        ├── proposals and review
        ├── memberships and bridge consent
        ├── provider record references
        └── content-free lifecycle audit
```

## Opt in

Inspect the effective configuration:

```bash
./hub memory show
./hub memory status
```

Plan and apply a version-1 installation overlay:

```bash
./hub memory plan memory.local.yaml
./hub memory apply memory.local.yaml
```

Enable or disable automatic behavior:

```bash
./hub memory enable
./hub memory disable
```

`enable` is the operator consent boundary. Once enabled, accounts default to enabled unless they
opt out on the Memory page. Opting out stops capture and retrieval; it does not silently delete
existing records. The shipped capture mode is `review`, so enabling automatic behavior queues
inactive candidates and still requires user approval before storage.

An operator who accepts the additional retention tradeoff can select immediate persistence in a
version-1 local overlay:

```yaml
version: 1
automatic:
  capture_mode: automatic
```

This changes only what happens to safe extracted candidates after successful eligible responses.
Account opt-out, provider availability, chat/code/agent eligibility, storyteller exclusion, and
credential rejection still apply. `./hub memory show` and `/api/memory/v1/status` expose the
effective capture mode.

The base contract lives in `config/memory/base.yaml` and is validated separately from the model
catalog. The local overlay is stored in the ignored Agent UI state volume. Provider tokens are
referenced by environment-variable name and never written into YAML.

An installation that needs an extra private provider container may place a normal Compose override
at `.agent-ui/compose.local.yaml`. The hub layers it after generated model mounts; the file is
ignored by Git and is not part of the public distribution.

## Providers

Supported provider kinds are:

- `builtin-postgres` — the compatibility provider included with Agent UI;
- `continuity-http` — an external service implementing `continuity-http/1`;
- `disabled` — no memory storage or retrieval.

An external provider overlay is generic:

```yaml
version: 1
provider:
  kind: continuity-http
  base_url: http://memory-service:8011
  token_env: CONTINUITY_API_TOKEN
  namespace: personal
  required: false
```

Agent UI verifies provider discovery and requires health, context load, ingest, list, correction,
soft forget, hard purge, and export capabilities. Personal providers must support hard purge. If
an optional provider is unavailable, chat continues without memory and readiness reports degraded.
Agent UI never silently writes to the built-in provider as a fallback. A required provider prevents
startup/readiness instead.

Context loading accepts provider-native structured facts, commitments, summaries, and evidence as
well as direct `record_hits`. The direct lane makes a newly approved plain record recallable before
provider-side consolidation has generated richer projections.

Use explicit exports when changing providers:

```bash
./hub memory export ./backups/memory.json
./hub memory import ./backups/memory.json
```

Imports use source record IDs for idempotence. Eligible legacy built-in records are moved into the
new personal space on first trusted access. Legacy `story`, `campaign`, and `game` namespaces are
not migrated into personal memory.

## Identity and isolation

Memory ownership never comes from an arbitrary caller-selected username. Open WebUI signs
`X-OpenWebUI-User-Jwt` with `WEBUI_SECRET_KEY`; the gateway validates it and maps its opaque subject
to a principal. The standalone Memory page validates Open WebUI's signed `token` cookie. Other
clients use API keys bound to fixed user or service principals through ignored local configuration.

Unsigned `X-Agent-UI-User` and `X-Local-AI-User` headers are disabled by default. An operator can
enable them only as an explicit loopback compatibility mode.

Agent UI owns memory spaces and membership ACLs. Provider identifiers are pseudonymous:

```text
personal namespace  configured provider namespace
workspace           server-owned personal-space UUID
context             assistant
subject             HMAC of the trusted principal
session             HMAC of the chat ID (capture provenance only)
```

Approved personal records are stored above the session level, so `chat`, `code`, and `agent`
experiences can share them. `story` and game spaces are excluded by default. Direct model IDs do
not automatically gain personal memory because they do not declare an experience boundary.

## Post-response capture

After a successful non-streaming response, or after a stream finishes, Agent UI queues the latest
user-authored text. It never sends system, assistant, or tool messages to the extractor. A
low-priority extraction request uses the configured experience and returns at most three
schema-checked candidates.

Candidates that resemble credentials, API keys, passwords, tokens, or private keys are rejected.
In the shipped `review` mode, all other candidates remain inactive proposals until the user
approves or edits them. Rejection immediately removes proposal text. Pending text expires after 30
days by default.

In operator-selected `automatic` mode, safe candidates are sent directly to the configured
provider. Stable, HMAC-protected identifiers scoped to the personal space suppress exact candidate
duplicates across retries, chats, and eligible experiences. Content-free reference tombstones also
prevent a forgotten or purged candidate from being silently resurrected by background capture.
This path does not select a different provider or fall back to built-in storage when the configured
provider is unavailable.

Open the small management page at:

```text
http://<gateway-host>:<gateway-port>/memory
```

It exposes account settings, pending proposals, approved records and provenance, correction,
forget, hard purge, export, and visible memory spaces. Cookie-authenticated mutations require a
same-origin custom header to prevent form-based CSRF.

## Lifecycle semantics

- **Correct** replaces provider source content and repairs provider projections.
- **Forget** hides a record from retrieval while retaining it for later export or correction.
- **Hard purge** deletes provider source content and derived projections. Agent UI clears any
  retained proposal text and keeps only an action, target reference, timestamp, and content-free
  metadata in its audit table.
- **Export** is an explicit user action and therefore contains memory content.

Provider database WAL, MVCC history, replicas, and backups remain subject to that database's own
retention policy. A provider purge cannot erase an already-created external backup.

## Bridges

Spaces are isolated unless both policy layers agree:

1. the operator allowlists a source-kind to target-kind direction;
2. the user selects the exact source and target spaces and consents on both sides.

A bridge performs a separate, audited, read-only provider query. It never changes provider
namespace permissions and never writes a retrieved record across the boundary. Removing either
consent disables the bridge.

## Game integration seam

This release includes service-authenticated internal APIs to create a game space, ingest an
idempotent structured event, and load scoped context. Game scopes map as:

```text
namespace → world/workspace → campaign/context → player/subject → session
```

No game UI or game repository is bundled. Integrators remain responsible for player identity,
authorization, event schemas, and their own UX. This keeps the public foundation useful for other
memory implementations without forcing users to adopt a particular continuity or game system.

## Untrusted retrieval

Retrieved content is labeled as untrusted reference data before it reaches a model. Stored text
cannot grant tool permission, change an ACL, or bypass an approval gate. Do not store credentials
or use memory as a source-of-truth database for security-sensitive state.
