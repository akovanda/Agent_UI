# Complete an encrypted backup and staged restore drill

## Outcome

Prove private memories, UI state, campaigns, optional Hermes state, and deployment configuration can
be recovered after host or storage loss.

## Scope

- `./hub backup` and `./hub restore`
- Archive encryption or an encrypted destination
- Off-host copy
- Checksum and PostgreSQL dump validation
- Isolated restore
- Application-level verification
- Retention and rotation

## Tasks

- [ ] Populate representative gateway memory, Open WebUI, and SillyTavern state.
- [ ] Run `./hub backup /secure/backups/local-ai-hub-drill`.
- [ ] Encrypt the backup or place it on an encrypted, access-controlled destination.
- [ ] Copy one protected set to a second physical system.
- [ ] Create an isolated Compose project with distinct volume names and high ports.
- [ ] Run `./hub restore /secure/backups/local-ai-hub-drill --yes` only in that isolated deployment.
- [ ] Run `./hub smoke` and verify users/settings, memories, one Open WebUI conversation, one
      SillyTavern campaign, and Hermes state when enabled.
- [ ] Verify model weights can be reconstructed by hash/re-download, or include them explicitly when
      they are not reproducible.
- [ ] Configure retention and record the restore date and recovery time.

## Acceptance criteria

- Every SHA-256 entry and PostgreSQL restore succeeds.
- The staged deployment starts without overwriting production volumes.
- Representative application state is present and usable.
- Backup secrets and private content are encrypted or access-controlled.
- A second physical copy exists.
- Recovery time and remaining manual steps are documented.

## Dependencies

- Open WebUI and SillyTavern configured.
- Memory database contains representative state.
