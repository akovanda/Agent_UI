CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- The governance repository is also used with external content providers, where
-- the legacy built-in `memories` table may intentionally not exist.
ALTER TABLE IF EXISTS memories ADD COLUMN IF NOT EXISTS forgotten_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS memory_user_settings (
    principal_id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL,
    capture_enabled BOOLEAN NOT NULL,
    retrieval_enabled BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_spaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'game')),
    owner_principal_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    provider_namespace TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, owner_principal_id, display_name)
);

CREATE TABLE IF NOT EXISTS memory_space_memberships (
    space_id UUID NOT NULL REFERENCES memory_spaces(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member', 'reader')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, principal_id)
);

CREATE TABLE IF NOT EXISTS memory_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES memory_spaces(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    content TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'rejected', 'expired')),
    source_experience TEXT NOT NULL,
    source_chat_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_proposals_owner_state_idx
    ON memory_proposals (principal_id, state, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_record_refs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id UUID NOT NULL REFERENCES memory_spaces(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    provider_record_id TEXT NOT NULL,
    proposal_id UUID REFERENCES memory_proposals(id) ON DELETE SET NULL,
    external_id TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'forgotten', 'purged')),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, provider_record_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_record_refs_external_idx
    ON memory_record_refs (space_id, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_bridge_consents (
    source_space_id UUID NOT NULL REFERENCES memory_spaces(id) ON DELETE CASCADE,
    target_space_id UUID NOT NULL REFERENCES memory_spaces(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    source_consented BOOLEAN NOT NULL DEFAULT FALSE,
    target_consented BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_space_id, target_space_id, principal_id),
    CHECK (source_space_id <> target_space_id)
);

CREATE TABLE IF NOT EXISTS memory_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memory_audit_principal_idx
    ON memory_audit (principal_id, created_at DESC);
