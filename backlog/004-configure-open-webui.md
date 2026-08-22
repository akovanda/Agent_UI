# Configure and harden Open WebUI for daily use

## Outcome

Provide a dependable ChatGPT-style interface for general chat, automatic routing, direct profile
selection, files, and later agent access.

## Scope

- Initial administrator setup
- Gateway OpenAI-compatible connection
- Profile presentation and defaults
- Authentication and remote access boundary
- Persistence and backup validation
- Optional Hermes connection after agent deployment

## Tasks

- [ ] Complete first-run administrator creation over loopback/private access.
- [ ] Verify `assistant`, `storyteller`, and `auto` appear.
- [ ] Set `assistant` as the daily default.
- [ ] Verify streaming, Markdown, code blocks, conversation history, and mobile browser behavior.
- [ ] Confirm persisted provider settings survive restart.
- [ ] Disable unused providers/features that create confusing or unsafe paths.
- [ ] Add Hermes as a second provider only after its acceptance tests pass.
- [ ] Restore Open WebUI data into a staging deployment.

## Acceptance criteria

- Unauthorized users cannot access the interface.
- The three profiles work without direct llama.cpp credentials.
- A restart preserves users, settings, and conversations.
- Backup/restore preserves representative state.
- Remote access works only through the selected private/authenticated path.

## Dependencies

- Validate llama.cpp router mode and fixed-model fallback.
