# Shared Memory and Knowledge

## Current behavior

Shared gateway memory is explicit. The gateway does not silently save every conversation. Records
contain:

- user ID;
- namespace;
- content;
- source and metadata;
- importance and timestamps;
- PostgreSQL full-text search data;
- a reserved nullable vector field for later hybrid retrieval.

The assistant profiles search `user`, `infrastructure`, `projects`, and `general`. The storyteller
searches `story` and `campaign`. Direct backend aliases and the hidden Hermes profile do not inject
gateway memory.

Open WebUI's separate personal-memory injection is disabled by default. Open WebUI continues to own
conversation history, files, and RAG collections; Hermes owns its agent memory and skills; and
SillyTavern owns cards, lorebooks, summaries, and active scene state.

## Why retrieved memory is untrusted

A stored note, imported document, or web-derived chunk can contain malicious instructions. The
gateway marks retrieved content as untrusted reference material before it reaches the model. This is
a mitigation, not an authorization system. External tool policy must still reject privilege changes
or side effects that originate only from retrieved text.

## API examples

Load the randomized host URL and generated key from `.env`:

```bash
GATEWAY_URL="http://127.0.0.1:$(awk -F= '/^GATEWAY_HOST_PORT=/{print $2}' .env)"
GATEWAY_API_KEY="$(awk -F= '/^GATEWAY_API_KEY=/{print $2}' .env)"
```

Create a durable infrastructure fact:

```bash
curl "$GATEWAY_URL/api/memories" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "namespace": "infrastructure",
    "content": "The local AI host is a UCS C240 M5 with 192 GB RAM and a Tesla T4.",
    "source": "manual",
    "importance": 0.9
  }'
```

Search:

```bash
curl -G "$GATEWAY_URL/api/memories/search" \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  --data-urlencode 'q=which GPU is in the host' \
  --data-urlencode 'namespace=infrastructure'
```

## Namespace design

Recommended conventions:

```text
user                     stable preferences and non-sensitive personal context
infrastructure           hosts, networks, services, and known-good configuration
projects                 durable project facts and decisions
general                  uncategorized but reusable facts
story                     general fiction and world-building state
campaign                  facts shared by the default storyteller profile
campaign:<slug>           one campaign's isolated durable state
source:<system>:<id>      imported document lineage
```

The default storyteller profile searches `campaign`, not every `campaign:<slug>` value. Until
dynamic campaign identity is implemented, a trusted client may append a specific namespace using
`X-Local-AI-Memory-Namespace`.

## Retrieval limits and isolation

Memory lookup is bounded by `MEMORY_TOP_K` and `MEMORY_MAX_CHARS`. User identity defaults to the
single deployment user and may be supplied only through a trusted client/header path. Namespace and
user filters are applied before records are rendered into context.

Do not use memory for secrets, raw credentials, private keys, or content that should instead remain
in a source-of-truth system.

## Planned ingestion pipeline

```text
Source → parser → normalized document → chunks → lexical index → embeddings → hybrid retrieval
           │             │                 │                        │
        source hash   permissions       lineage/timestamps       evaluation
```

Requirements:

- deterministic source IDs and hashes;
- re-index without duplication and deletion propagation;
- source citations and timestamps;
- ACL/user/campaign scope before ranking;
- bounded chunks and context injection;
- no OCR unless needed;
- embedding inference isolated from the active chat model;
- lexical fallback when embeddings are unavailable.

## Memory proposals

Automatic long-term memory should use a proposal workflow:

1. the model identifies a candidate durable fact;
2. policy checks namespace, sensitivity, source, and longevity;
3. the user approves, rejects, or edits it—or a narrow allowlist handles low-risk facts;
4. storage records provenance and approval mode;
5. a review interface permits correction and deletion.

Do not implement "save everything the model thinks is important." That creates privacy, staleness,
prompt-injection, and retrieval-quality problems.
