# Open WebUI Configuration

Open WebUI is the general-purpose browser and mobile interface. It talks to the Local AI Hub
gateway—not directly to llama.cpp—so profile routing, GPU serialization, shared memory,
authentication, and response metadata remain consistent across clients.

## Seeded connection

Compose starts Open WebUI with:

```text
Base URL: http://gateway:8000/v1
API key:  GATEWAY_API_KEY
Default:  assistant
```

After first launch:

1. create the initial local administrator;
2. set `OPEN_WEBUI_ENABLE_SIGNUP=false` in `.env`;
3. recreate Open WebUI with `./hub compose up -d --force-recreate open-webui`;
4. confirm the model selector contains `assistant`, `assistant-fast`, `assistant-deep`,
   `storyteller`, and `auto`;
5. send one streamed request to each relevant profile;
6. verify the gateway logs and response headers report the intended backend.

Open WebUI persists provider configuration in PostgreSQL and local application data in its named
Docker volume. Environment variables seed the initial configuration; administrator changes in the
UI become persistent application state and must be included in backup/restore testing.

## Memory ownership

The gateway is the authoritative cross-interface memory layer. Its records are scoped by user and
namespace and are injected as explicitly untrusted reference material. To avoid two independent
systems silently injecting competing facts, the default deployment sets:

```text
OPEN_WEBUI_ENABLE_MEMORIES=false
```

Open WebUI still owns:

- conversation history;
- uploaded files and RAG metadata;
- user and account settings;
- provider and model-display configuration;
- its own pgvector-backed document collections.

The gateway owns durable cross-client memories. SillyTavern owns campaign-native character cards,
lorebooks, summaries, and active-scene state. Open WebUI memory can be deliberately enabled later,
but that changes the prompt and governance model and should be evaluated as a separate feature.

## Recommended profiles

| Profile | Intended use |
|---|---|
| `assistant` | Balanced daily chat, knowledge, coding, and technical work |
| `assistant-fast` | Routine interactions with lower reasoning effort |
| `assistant-deep` | Difficult debugging, planning, and technical synthesis |
| `storyteller` | Explicit creative writing from the general UI |
| `auto` | Deterministic assistant/story selection when manual choice is unnecessary |

Direct backend aliases and the hidden `hermes-agent` gateway profile are operational interfaces,
not ordinary Open WebUI choices.

## Hermes connection

After the normal gateway path is stable, add Hermes as a visibly separate OpenAI-compatible
provider:

```text
Base URL: http://hermes:8642/v1
API key:  HERMES_API_KEY
```

Hermes itself routes its model calls back through the gateway's hidden `hermes-agent` profile. Do
not replace the normal gateway provider with Hermes; ordinary chat must remain available when the
agent runtime is stopped or misconfigured.

## Private remote access

All host ports bind to loopback by default. For a phone or another computer, use Tailscale, a VPN,
an SSH tunnel, or an authenticated TLS reverse proxy. Preserve Open WebUI authentication and apply
network-edge authentication/rate limits where appropriate.

Do not expose the gateway, llama.cpp, or PostgreSQL ports directly to the public internet, and do
not place `GATEWAY_API_KEY` in public browser-side configuration.

## Backup and validation

Use the containerized backup command:

```bash
./hub backup /secure/backups/local-ai-hub
```

The command quiesces Open WebUI, dumps its PostgreSQL database, and archives its named data volume.
After an upgrade or restore, verify:

- account sign-in and signup policy;
- provider URL and API key;
- advertised profiles;
- one streamed assistant response;
- one prior conversation and uploaded file;
- one RAG collection/query;
- persistence across a container restart.
