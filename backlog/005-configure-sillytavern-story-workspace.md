# Configure SillyTavern and a reusable story workspace

## Outcome

Provide a purpose-built Stheno interface with stable character, lore, scene, and campaign context.

## Scope

- Gateway/OpenAI-compatible endpoint
- `storyteller` profile
- Generation preset
- Character/persona template
- World Info/lorebook template
- Author note and session-summary workflow
- Persistence and backup

## Tasks

- [ ] Connect SillyTavern to the gateway and select `storyteller`.
- [ ] Create a named sampler preset matching the repository baseline.
- [ ] Create reusable GM/character/persona templates.
- [ ] Create World Info categories for factions, locations, party, NPCs, assets, threats, history,
      and current arc.
- [ ] Define what remains in SillyTavern versus durable gateway campaign memory.
- [ ] Test a multi-session campaign with a 16–32K active context.
- [ ] Verify restart and staged restore preserve campaign state.

## Acceptance criteria

- Stheno streams reliably through SillyTavern.
- Character voice and established facts remain stable in a representative multi-scene test.
- The system does not dictate player choices.
- Lore entries activate only when relevant.
- Campaign state survives restart and restore.

## Dependencies

- Validate llama.cpp router mode and fixed-model fallback.
