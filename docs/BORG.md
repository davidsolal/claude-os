# 🛸 The Borg — Collective Orchestration for Claude OS

> *"We are the Borg. Your distinctiveness will be added to our own. Resistance is futile."*

The Borg turns Claude OS into a **hive mind**: a Queen orchestrator decomposes a
plan and assimilates **drones** — both internal Claude sub-agents and external
**ollama-backed Claude** processes — to execute it in parallel. Every drone shares
**one mind**: the same Claude OS knowledge bases. That shared memory is what makes
many models behave as a single collective.

## The pieces

| Piece | Path | Role |
|-------|------|------|
| **Queen agent** | `templates/agents/borg-queen.md` → `~/.claude/agents/` | The orchestrator persona. Decomposes plans, dispatches drones, consolidates. |
| **`/borg` command** | `templates/commands/borg.md` → `~/.claude/commands/` | Top-level entry. Runs the full hybrid swarm (internal `Task` drones **and** external ollama drones). |
| **Drone launcher** | `scripts/borg-drone.sh` | Spawns one sandboxed external ollama drone, headless; auto-recalls, executes, assimilates. |
| **Collective lib** | `scripts/borg-lib.sh` | `curl` primitives: `borg_recall`, `borg_recall_kb`, `borg_assimilate`, `borg_ensure_kb`. |
| **Hive KB** | `borg-collective` (Claude OS KB) | Durable cross-run shared memory. |
| **Sandboxes** | `~/.claude/borg/runs/<run-id>/<drone>/` | Per-drone working dirs. |

## The Borg protocol (every drone, every task)

1. **RECALL** — query the collective (`search_all_knowledge_bases`) before acting.
2. **EXECUTE** — do the task, confined to the drone's sandbox.
3. **ASSIMILATE** — write a `report.md` back into `borg-collective` (+ the project KB).

Internal and external drones both follow this loop, so the swarm converges on one
shared body of knowledge regardless of which model ran the work.

## Usage

```text
/borg <your plan, or a path to a spec/plan file>
```

The Queen will: pre-flight the collective → recall prior knowledge → decompose into
independent tasks → assign drones (internal Claude for reasoning, external ollama
for parallel grunt work) → dispatch → assimilate reports → consolidate → write a
collective summary for next time.

### Launch a single external drone by hand

```bash
RUN_ID=$(date +%Y%m%d-%H%M%S)-borg
bash scripts/borg-drone.sh \
  --run-id "$RUN_ID" --name drone-tests \
  --task "Write pytest tests for app/core/rag_engine.py" \
  --model glm-5:cloud \
  --project-kb myapp-project_memories   # omit if not in a project
# --readonly  → recon/planning only, no file writes
```

## Models

Default external drone model: **`glm-5:cloud`**. Other installed cloud models:
`glm-4.6:cloud`, `gpt-oss:120b-cloud`, `deepseek-v3.1:671b-cloud`. Any installed
local model (e.g. `llama3.1:8b`) also works but needs ≥64k context to drive the
Claude Code agent well. Override per drone with `--model`.

## Safety

- External drones run `--dangerously-skip-permissions` but are **confined to their
  sandbox dir** via `--add-dir`. Never sandbox a drone at your project root.
- Drones produce artifacts in their sandbox; the Queen (or you) reviews reports and
  applies vetted changes to live code. Recon tasks use `--readonly`.

## Requirements

- Claude OS server running (`./start.sh`, health at `http://localhost:8051/health`).
- Claude OS auth disabled (default) **or** `export BORG_AUTH_TOKEN=<jwt>` for drones.
- `ollama` ≥ 0.24 with the `launch` sub-command; `claude` CLI on `PATH`.

## Config (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `CLAUDE_OS_API` | `http://localhost:8051` | Collective API base URL. |
| `BORG_COLLECTIVE_KB` | `borg-collective` | Hive KB name. |
| `BORG_AUTH_TOKEN` | _(unset)_ | Bearer token, only if Claude OS auth is enabled. |
