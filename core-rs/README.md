# Hestia Rust Core

A Rust API and web admin replacing the Python Hestia gateway. It preserves
the existing `/v1/chat`, `/v1/route`, `/v1/tools`,
`/v1/audio/ask`, `/v1/audio/speak`, `/v1/audio/transcribe` routes.
The existing Python `listener` and `stt` services continue to work.

- **Rust:** Axum HTTP, FastEmbed multilingual E5 routing, YAML workflow
  execution, JSON input validation and a scheduled-workflow runner.
- **Plugins:** `tool.yaml` + optional language-agnostic `exec.call` program,
  stdin JSON / stdout JSON. Legacy `python.call` remains supported.
- **Web:** tool activation/configuration, secrets with redacted reads, LLM
  endpoint/model settings, recurring jobs with fixed args, execution logs.
- **Security:** only run trusted tool scripts. Subprocess isolation here is
  *not* a sandbox. Keep the dashboard on loopback or a trusted private LAN,
  protect the HTTP API with `HESTIA_API_KEY`. Never publish publicly.
- **Scheduling:** UTC cron expressions (5-field or 6-field); only tools
  marked `schedule_allowed: true` may be scheduled. Scheduled runs never
  bypass confirmation or secret requirements.

Run locally with `cargo run --manifest-path core-rs/Cargo.toml` (requires
model downloads), or use the Docker Compose instructions at
`docs/rust-core.md`.
