# Project instructions for Copilot

Read [AGENTS.md](../AGENTS.md) before changing this repository. It is the shared operating guide for architecture, upstream preservation, code ownership and validation commands.

The actual stack is Python/Quart under `api`, `rag`, `agent`, React/TypeScript/Vite under `web`, and Go under `cmd`/`internal`. Preserve the upstream structure, isolate owned business logic, and keep changes to standard code justified and local.

Use repository commands and prepared environments. Python checks are targeted pytest/Ruff; frontend commands come from `web/package.json`; Go tests use `build.sh` with its native dependencies. See [REGRESSION.md](../test/REGRESSION.md) for the existing lanes and their environment requirements. Do not substitute a generic app layout or run live suites against shared data.
