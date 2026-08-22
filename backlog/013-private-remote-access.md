# Implement private authenticated remote access

## Outcome

Allow secure iPhone, Mac, and browser access without publishing local AI services directly to the
internet or LAN.

## Scope

- Tailscale-first access
- Optional reverse proxy and friendly private hostnames
- TLS/authentication/rate limiting where applicable
- Firewall verification
- Mobile usability
- Recovery access

## Tasks

- [ ] Decide between SSH/Tailscale tunnels and a private reverse proxy.
- [ ] Keep Compose host binding on loopback unless a reviewed design requires otherwise.
- [ ] Configure private DNS names for Open WebUI and SillyTavern.
- [ ] Add TLS and strong authentication at the proxy when used.
- [ ] Apply request/body/time limits and websocket/SSE support.
- [ ] Verify firewall rules from trusted and untrusted LAN clients.
- [ ] Test iPhone and Mac access, streaming, uploads, and reconnect behavior.
- [ ] Document recovery path when Tailscale/proxy is unavailable.

## Acceptance criteria

- Authorized devices can reach intended UIs and stream responses.
- Untrusted LAN/internet clients cannot reach gateway, llama.cpp, PostgreSQL, Hermes, or UIs.
- Direct backend ports remain diagnostic-only.
- Authentication and TLS survive restart.
- Recovery access does not require weakening normal exposure.

## Dependencies

- Configure Open WebUI and SillyTavern.
