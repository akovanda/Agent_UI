# Open WebUI

Open WebUI is Agent UI's default human-facing interface. It connects only to the authenticated gateway, not directly to local or remote inference backends.

## Connection

Compose configures:

```text
OPENAI_API_BASE_URL=http://gateway:8000/v1
OPENAI_API_KEY=<GATEWAY_API_KEY>
```

The model picker receives stable experience IDs and directly registered model IDs from `/v1/models`.

## Experience entries

Typical entries:

- `chat`
- `code`
- `story`
- `image`
- `agent`

An experience may appear with `available: false` before a capable model is registered. Setup state is visible at `/api/setup/status`; inference fails with a clear `model_unavailable` error rather than selecting an incompatible model.

## Workspace models

Open WebUI Workspace Models can layer UI-specific prompts, knowledge, tools, and display names on top of Agent UI experiences. Keep the underlying model field stable:

```text
base model: code
workspace name: Repository Engineer
```

This avoids coupling Open WebUI exports to a physical model name.

## Files and RAG

Open WebUI can manage its own document collections and uses PostgreSQL/pgvector in the reference stack. An embedding-capable model may be registered through Agent UI, or Open WebUI may use its own embedding configuration.

Treat retrieved text as untrusted source material. Do not place tool credentials or privileged system instructions inside documents.

## Private SearXNG web search

Web search is off by default. The optional `web-search` Compose profile runs a pinned SearXNG container on the separate `agent-ui-tools` network and gives it no host `ports` mapping. Open WebUI reaches it only at:

```text
http://searxng:8080/search
```

For a fresh or upgraded checkout, initialize once and then start the profile:

```bash
./hub init
./hub up --web-search
```

Re-running `./hub init` preserves existing ports and secrets unless a rotation flag is supplied; it adds the generated `SEARXNG_SECRET_KEY` required by the new profile. `--web-search` both starts SearXNG and recreates Open WebUI with `ENABLE_WEB_SEARCH=true`. Normal `./hub up` keeps the feature disabled.

The shipped settings enable SearXNG's JSON response format, which Open WebUI requires. The query URL intentionally has no `?q=<query>` suffix: Open WebUI 0.11.0 appends the query and `format=json` parameters itself. Optional defaults are controlled in `.env`:

```text
OPEN_WEBUI_ENABLE_WEB_SEARCH_CONFIRMATION=true
OPEN_WEBUI_WEB_SEARCH_RESULT_COUNT=3
OPEN_WEBUI_SEARXNG_LANGUAGE=all
```

In Open WebUI 0.11.0, enable **Web Search** for the selected Workspace Model when using native function calling, then turn on the per-chat Web Search control. The global provider configuration remains environment-controlled because the reference stack sets `ENABLE_PERSISTENT_CONFIG=false`.

Check the private service with `./hub status` or `./hub logs searxng`. To stop it and return Open WebUI to the disabled default, recreate the default stack:

```bash
./hub down
./hub up
```

SearXNG is private from the host and public network, but searches still send queries from SearXNG to the selected public search engines, and Open WebUI fetches result pages. Treat search terms and retrieved pages as external data flows.

## Recommended tool baseline

Start the complete low-risk tool bundle with:

```bash
./hub init
./hub up --tools
```

The bundle enables or exposes:

- SearXNG-backed `search_web` plus Open WebUI's `fetch_url` support;
- browser-side Pyodide code execution and code interpretation;
- PostgreSQL/pgvector-backed knowledge and file retrieval;
- memories, notes, tasks, calendar, and automations; and
- an Open Terminal scratch environment for shell, Git, file, and process operations.

Native tool calling remains model-dependent. The shipped local GPT-OSS catalog model advertises
tool support through Ollama, but Web Search, Code Interpreter, and Open Terminal still have
per-chat controls in Open WebUI. Enable the tools you want from the **+** menu beside the prompt.
Workspace Model defaults can make selected tools active for every new chat.

### Isolated Open Terminal

`--tools` includes the terminal profile; `./hub up --terminal` starts only that optional service.
The reference service uses the pinned slim Open Terminal image and is intentionally constrained:

- no host port, host-directory mount, Docker socket, sudo, package installer, or Docker CLI;
- a dedicated persistent scratch volume mounted only at `/home/user`;
- a separate tools network shared with Open WebUI, not the database/gateway network;
- a server-to-server API connection with CORS limited to the Open WebUI service origin;
- 2 CPUs, 2 GiB of memory, and 256 processes at most; and
- outbound DNS restricted to the configured GitHub and GitLab domains (including subdomains).

Open WebUI receives the terminal URL and API key server-side. The key does not need to be entered
in the browser. `./hub init` generates `OPEN_TERMINAL_API_KEY`; the terminal connection is removed
again when the default stack is recreated without `--terminal` or `--tools`.

The terminal is suitable for scratch files, cloning and inspecting public repositories, shell
automation, and creating downloadable artifacts. It deliberately cannot operate on the host
checkout. Add a narrowly scoped bind mount only after reviewing the access that it would grant.

The remaining productivity tools are built into Open WebUI 0.11.0 and do not install third-party
Python plugins. Workspace Tools and Functions execute arbitrary code inside Open WebUI itself;
review their source before importing any community extension.

## Images

Open WebUI can use Agent UI's `/v1/images/generations` route when an image-capable backend is registered. It can also connect directly to an image workflow service using Open WebUI's native image settings. The latter is useful when the workflow engine exposes controls not represented by the OpenAI image request format.

## Code

Select the `code` experience for code chat and review. Tools or IDE clients may call the same gateway through chat, completions, or infill routes when the selected model declares the corresponding capability.

## Story work

The `story` experience is available in Open WebUI for ordinary creative chat. Use the optional story workspace when character cards, lorebooks, group roleplay, or scene-specific context management are required.

## Reasoning effort

Open WebUI request parameters may include `reasoning_effort`; custom functions or clients can set `X-Reasoning-Effort`. Agent UI maps the stable value through the selected model's feature declaration.

Operators can also register multiple advertised model IDs that share an `upstream_model` and set a
different model-level `defaults.reasoning_effort` on each. Those aliases appear separately in the
model picker, while request parameters and experience defaults still override the alias preset.

## Tools

A model's `features.tools: true` declaration means the backend can represent tool calls. It does not grant access. Configure tools in Open WebUI and begin with read-only operations. Require approval for writes, shell commands, infrastructure changes, and external communications.

## Memory

The reference Compose stack disables Open WebUI's independent personal memory by default:

```text
OPEN_WEBUI_ENABLE_MEMORIES=false
```

Agent UI automatic memory is also off until `./hub memory enable`; these are independent switches.
Running both systems is possible, but duplicates and conflicting facts are harder to reason about.
Agent UI uses review-first proposals and exposes them at the gateway `/memory` page. The browser can
reuse Open WebUI's signed login cookie when the gateway is reached through the same host name.

## Users

Open WebUI authentication is enabled. The first account becomes administrator in the standard
first-run flow. Compose enables signed user-info forwarding: Open WebUI sends
`X-OpenWebUI-User-Jwt`, signed with `WEBUI_SECRET_KEY`, on gateway model calls. Agent UI validates
the token and derives a pseudonymous provider subject. Do not replace it with a browser-supplied or
reverse-proxy-supplied username header.

## Exposure

Open WebUI binds to loopback by default. For remote access, use a VPN or authenticated TLS reverse proxy. Do not expose the gateway or underlying inference services merely because the UI has authentication.
