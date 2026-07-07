# 🛸 Borg — collective orchestration for Claude Code

> *"We are the Borg. Your distinctiveness will be added to our own. Resistance is futile."*

A **Queen** orchestrator decomposes a plan and assimilates **drones** — internal
Claude sub-agents and external **ollama-backed Claude** processes — to execute it in
parallel. Every drone shares **one mind**: the same knowledge bases. That shared
memory is what makes many models behave as a single collective.

## What's in the package

| Component | Path | Role |
|-----------|------|------|
| `/borg` command | `commands/borg.md` | Top-level entry. Runs the full hybrid swarm. |
| `borg-queen` agent | `agents/borg-queen.md` | The orchestrator persona / subagent. |
| Drone launcher | `scripts/borg-drone.sh` | Spawns one sandboxed external ollama drone; auto-recalls, executes, assimilates. |
| Collective lib | `scripts/borg-lib.sh` | `curl` primitives: `borg_recall`, `borg_recall_kb`, `borg_assimilate`, `borg_ensure_kb`, `borg_online`. |

Scripts are resolved at runtime via `${CLAUDE_PLUGIN_ROOT}`, so the plugin works
from wherever Claude Code installs it.

## Install

```text
/plugin marketplace add /path/to/claude-os/borg-plugin
/plugin install borg@borg
```

(Or, once published to its own git repo: `/plugin marketplace add <owner>/<repo>`.)

Then run:

```text
/borg <your plan, or a path to a spec/plan file>
```

## The Borg protocol (every drone, every task)

1. **RECALL** — query the collective before acting.
2. **EXECUTE** — do the task, confined to the drone's sandbox.
3. **ASSIMILATE** — write a `report.md` back into `borg-collective` (+ the project KB).

## Requirements

- A **memory backend** speaking the HTTP contract below — defaults to
  [Claude OS](https://github.com/brobertsaz/claude-os) (`./start.sh`, health at
  `http://localhost:8051/health`).
- `ollama` ≥ 0.24 with the `launch` sub-command (for external drones).
- `claude` CLI on `PATH`.
- Backend auth disabled (default) **or** `export BORG_AUTH_TOKEN=<jwt>` for drones.

The `mcp__code-forge__*` tools referenced by the command/agent are the Claude OS MCP
convenience layer. If they aren't connected, the collective is still fully driveable
through the `curl` helpers in `scripts/borg-lib.sh` (which need only `CLAUDE_OS_API`).

## Pluggable memory backend — the contract

Borg is **not** hard-wired to Claude OS. Any service that implements these endpoints
at `CLAUDE_OS_API` can be the hive mind. This is exactly what `scripts/borg-lib.sh`
and `scripts/borg-drone.sh` depend on:

| Method & path | Used by | Purpose |
|---------------|---------|---------|
| `GET /health` | `borg_online` | Liveness check (2xx = up). |
| `GET /api/kb` | `borg_ensure_kb` | List KBs → `{ "knowledge_bases": [{ "name": ... }] }`. |
| `POST /api/kb` | `borg_ensure_kb` | Create a KB. Body: `{ "name", "kb_type", "description" }`. |
| `POST /api/kb/search-all` | `borg_recall` | Cross-KB semantic search. Body: `{ "query", "top_k", "kb_filter"? }` → `{ "results": [{ "kb_name", "score", "text" }] }`. |
| `POST /api/kb/{kb}/chat` | `borg_recall_kb` | RAG answer over one KB. Body: `{ "query" }` → `{ "answer" }` (or `response`). |
| `POST /api/kb/{kb}/upload` | `borg_assimilate` | Upload a markdown file as memory. `multipart/form-data` field `file`. |

To target a different backend, either point `CLAUDE_OS_API` at a service that
implements the above, or fork `scripts/borg-lib.sh` to map these calls onto your
memory plugin's API. Nothing else in the plugin reaches the backend directly.

## Config (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `CLAUDE_OS_API` | `http://localhost:8051` | Memory backend base URL. |
| `BORG_COLLECTIVE_KB` | `borg-collective` | Hive KB name. |
| `BORG_AUTH_TOKEN` | _(unset)_ | Bearer token, only if the backend enables auth. |

## Models

Default external drone model: **`glm-5:cloud`**. Other common cloud models:
`glm-4.6:cloud`, `gpt-oss:120b-cloud`, `deepseek-v3.1:671b-cloud`. Any installed
local model works too but needs ≥64k context to drive the Claude Code agent well.
Override per drone with `--model`.

## Safety

- External drones run `--dangerously-skip-permissions` but are **confined to their
  sandbox dir** (`~/.claude/borg/runs/<run-id>/<drone>/`) via `--add-dir`. Never
  sandbox a drone at your project root.
- Drones produce artifacts in their sandbox; the Queen (or you) reviews reports and
  applies vetted changes to live code. Recon tasks use `--readonly`.

### Launch a single external drone by hand

```bash
RUN_ID=$(date +%Y%m%d-%H%M%S)-borg
bash "${CLAUDE_PLUGIN_ROOT:-.}/scripts/borg-drone.sh" \
  --run-id "$RUN_ID" --name drone-tests \
  --task "Write pytest tests for app/core/rag_engine.py" \
  --model glm-5:cloud \
  --project-kb myapp-project_memories   # omit if not in a project
# --readonly  → recon/planning only, no file writes
```
