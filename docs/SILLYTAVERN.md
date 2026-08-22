# SillyTavern Story Configuration

SillyTavern is the story-native interface. It should use the Local AI Hub gateway's `storyteller`
profile so Stheno receives its own prompt, sampler defaults, memory namespaces, and one-GPU model
coordination.

## Endpoint

Configure an OpenAI-compatible backend:

```text
API URL:   <browser-reachable gateway URL>/v1
API key:   GATEWAY_API_KEY
Model:     storyteller
Streaming: enabled
```

SillyTavern's browser must be able to reach the configured URL. `http://gateway:8000/v1` works for
server-side/container communication but not from a browser on another machine. Use the gateway URL
shown by `./hub ports`, or a reviewed Tailscale/reverse-proxy URL, and configure the corresponding
CORS origin when direct browser-to-gateway access is required.

## Division of state

### SillyTavern owns

- character cards and example dialogue;
- user persona/player character;
- World Info and lorebooks;
- Author's Note;
- chat transcripts and group-chat speaker logic;
- current-scene state;
- campaign summaries prepared for active context.

### Gateway memory owns

- durable campaign decisions that must survive replacing a chat;
- stable relationships and faction status shared across interfaces;
- ship, base, and inventory facts referenced outside SillyTavern;
- high-level session summaries with provenance and timestamps.

Do not copy an entire lorebook into gateway memory. Duplicated sources create conflicts and consume
context without improving continuity.

## Starting sampler

The `storyteller` profile begins with:

```text
temperature:    1.25
top_p:          0.95
min_p:          0.12
repeat_penalty: 1.08
```

SillyTavern values sent explicitly override gateway defaults. Keep named, exported presets so
quality changes are attributable and reversible.

## Campaign structure

A useful World Info layout is:

```text
Global rules and tone
Factions and politics
Locations
Player party
Recurring NPCs
Ships, vehicles, and assets
Active threats
Resolved history
Current arc
```

Use keyword-triggered entity lore plus a short always-on current-arc summary. A focused 16K–32K
active context with structured summaries usually gives the model a clearer job than indiscriminately
sending every historical turn.

## Player agency

The repository storyteller prompt instructs the model not to dictate the player's choices. Add
campaign-specific tone and rules in SillyTavern without removing that boundary. For tactical play,
provide plausible options and what a trained character would reasonably know while leaving intent
and final decisions to the player.

## Backups

SillyTavern state lives in four named Docker volumes: config, data, plugins, and third-party
extensions. Back them up through the normal control plane:

```bash
./hub backup /secure/backups/local-ai-hub
```

A restore drill should verify at least one character card, lorebook, generation preset, prior chat,
and continuing campaign—not only that the volumes were recreated.
