# Upstream References and Pinning Notes

This repository integrates fast-moving projects. Validate current upstream behavior before
updating image tags, command-line flags, or configuration formats.

## Primary upstreams

- llama.cpp server documentation and source: `ggml-org/llama.cpp`
  - OpenAI-compatible chat endpoints
  - router mode and model preset INI format
  - `/models`, `/models/load`, `/models/unload`
  - GPU offload, MoE CPU placement, KV cache, flash attention, and fit controls
- Open WebUI documentation and source: `open-webui/open-webui`
  - OpenAI-compatible provider connections
  - persisted admin connection settings
  - workspace models, tools, knowledge, and memory
- SillyTavern documentation and source: `SillyTavern/SillyTavern`
  - Docker bind mounts
  - OpenAI-compatible/custom endpoints
  - character cards, World Info/lorebooks, personas, and author notes
- Hermes Agent documentation/source from Nous Research
  - custom OpenAI-compatible model providers
  - API-server mode
  - Open WebUI connection
  - Docker deployment and persistent state
- GPT-OSS GGUF: `ggml-org/gpt-oss-20b-GGUF`
- Stheno GGUF: `bartowski/Llama-3.1-8B-Stheno-v3.4-GGUF`

## Licensing

- Repository code is private and currently has no open-source license grant.
- GPT-OSS weights retain their upstream license and notices.
- The selected Stheno checkpoint is marked CC BY-NC 4.0. Do not use it for commercial purposes
  without a compatible license or model replacement.
- Open WebUI, SillyTavern, Hermes, llama.cpp, PostgreSQL/pgvector, and container images retain their
  own licenses. Preserve required notices when redistributing images or code.

## Pinning policy

Moving tags in `.env.example` are bootstrapping conveniences. Once validated on the UCS:

1. record the exact image digest;
2. record NVIDIA driver and Container Toolkit versions;
3. record GGUF SHA-256 hashes;
4. record gateway commit SHA and profile configuration;
5. record benchmark and switch-regression results;
6. update the deployment baseline before promoting.

Pin by digest in production. Keep the previous known-good digest until rollback is proven.
