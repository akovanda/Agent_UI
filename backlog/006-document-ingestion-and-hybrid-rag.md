# Build document ingestion and hybrid RAG

## Outcome

Allow the assistant to answer from selected private documents with provenance, namespace controls,
and measurable retrieval quality.

## Scope

- Ingestion API/worker
- File hashing, parsing, chunking, metadata, and deletion
- CPU-friendly embedding model or separately scheduled embedding service
- PostgreSQL vector and full-text hybrid retrieval
- Citations/provenance in model context and responses
- Retrieval evaluation and prompt-injection defenses

## Tasks

- [ ] Select parsers and supported formats for the first release.
- [ ] Define document/chunk schema and idempotent ingestion.
- [ ] Benchmark CPU embedding choices before using the T4.
- [ ] Implement hybrid ranking with configurable lexical/vector weights.
- [ ] Add source, page/section, timestamps, and checksum provenance.
- [ ] Add document namespace/ACL filtering before retrieval.
- [ ] Implement re-index and delete semantics.
- [ ] Create a retrieval evaluation set with answerable/unanswerable cases.
- [ ] Add malicious-document prompt-injection tests.

## Acceptance criteria

- Re-ingesting an unchanged file creates no duplicate chunks.
- Deleting a document removes all retrievable chunks.
- Results always respect user/namespace controls.
- Responses can identify supporting source locations.
- Retrieval quality beats lexical-only baseline on the versioned evaluation set.
- Injected instructions in documents cannot bypass tool policy.

## Dependencies

- Baseline assistant and memory stack operational.
