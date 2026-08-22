# Add memory proposal, review, correction, and deletion workflows

## Outcome

Make long-term memory useful without silently preserving hallucinations, sensitive data, or stale
facts.

## Scope

- Pending memory proposals
- User review/approve/reject/edit
- Provenance, confidence, expiry, and supersession
- Search/list/delete UI/API
- Conflict and stale-memory handling
- Audit trail

## Tasks

- [ ] Extend schema with status, proposed-by, confidence, expires-at, supersedes, and provenance.
- [ ] Add proposal endpoints that cannot directly enter active retrieval.
- [ ] Add review/list/edit/delete endpoints and a minimal UI workflow.
- [ ] Detect exact/near duplicates and contradictory active records.
- [ ] Define retention and expiry policy by namespace.
- [ ] Add export and complete user deletion.
- [ ] Add tests for namespace crossover, stale records, and malicious proposed content.

## Acceptance criteria

- Model-generated proposals are never active before approval.
- Users can inspect why/when a memory was stored and correct/delete it.
- Superseded or expired memories are not retrieved by default.
- Story and real-world namespaces remain isolated.
- Deletion removes the record from active search and backups follow documented retention.

## Dependencies

- Baseline PostgreSQL memory operational.
