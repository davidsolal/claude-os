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
| **Drone launcher** | `scripts/borg-drone.sh` | Spawns one sandboxed external ollama drone, headless; auto-recalls, executes, assimilates. Kills hung drones after `--timeout` (default 1800s). |
| **Swarm launcher** | `scripts/borg-swarm.sh` | Fans a task list out across many drones with bounded parallelism; prints a summary table and assimilates it. |
| **Collective lib** | `scripts/borg-lib.sh` | `curl` primitives: `borg_recall`, `borg_recall_kb`, `borg_assimilate`, `borg_verify_doc`, `borg_ensure_kb`. |
| **Selftest** | `scripts/borg-selftest.sh` | End-to-end smoke test against a mock API + fake `ollama` — no server, no models, no cost. Auto-finds a non-noexec temp dir. |
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

### Launch a whole swarm from a task list

```bash
cat > tasks.txt <<'EOF'
drone-auth :: Refactor the auth module to use JWT
drone-tests :: Write pytest tests for app/core/rag_engine.py
Audit error handling in mcp_server/server.py      # unnamed → drone-3
EOF
bash scripts/borg-swarm.sh --tasks tasks.txt --parallel 3 \
  --project-kb myapp-project_memories   # omit if not in a project
# per-run flags: --model, --timeout <secs>, --readonly, --run-id
```

One drone per line (`name :: task`, or just the task). The swarm launcher waits
for every drone, prints a `drone · rc · task · report` table, writes
`swarm-summary.md` into the run dir, and assimilates it into the hive.

### Launch a single external drone by hand

```bash
RUN_ID=$(date +%Y%m%d-%H%M%S)-borg
bash scripts/borg-drone.sh \
  --run-id "$RUN_ID" --name drone-tests \
  --task "Write pytest tests for app/core/rag_engine.py" \
  --model glm-5:cloud \
  --project-kb myapp-project_memories   # omit if not in a project
# --readonly       → recon/planning only, no file writes
# --timeout <secs> → kill a hung drone (default 1800; rc=124 on expiry)
```

Reports upload under a unique filename (`<run-id>-<drone>.md`) so sibling
drones never collide in the KB. `--readonly` drones get a read-only tool
allowlist (instead of `--dangerously-skip-permissions`) and return their report
via stdout markers, which the launcher extracts into `report.md`.

### Verify the pipeline without spending anything

```bash
bash scripts/borg-selftest.sh
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

## Lineage — the Hermes borg

This shell/Claude-Code borg has a sibling lineage running natively on the
Hermes Agent: [`hermes-borg-skill`](https://github.com/davidsolal/hermes-borg-skill)
(orchestration skill + 180 battle-tested references) and
[`hermes-borg-plugin`](https://github.com/davidsolal/hermes-borg-plugin)
(python toolset: `borg_status` / `borg_recall` / `borg_assimilate` /
`borg_verify_doc` / `borg_ensure_kb`). Both lineages share the same Claude OS
API contract and the same RECALL → EXECUTE → ASSIMILATE protocol.

Rules reconciled from the Hermes lineage into this one:
- **KB identifiers**: API paths take the KB `name` (underscored form from
  `GET /api/kb`), never the slug.
- **Never trust an assimilate 200** — an upload can succeed while the document
  never appears (embedding lag, write race). `borg_verify_doc` checks the
  documents list; the drone launcher does this automatically.
- **Drone reports are self-reports** — the Queen re-reads files a drone claims
  to have modified and runs the tests herself before applying patches.
- **Drones write reports early and append** — a budget/timeout cut-off must not
  lose everything (the launcher's prompt enforces this).

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
| `BORG_DRONE_TIMEOUT` | `1800` | Per-drone wall-clock limit in seconds (`--timeout` overrides). |
| `BORG_RUNS_DIR` | `~/.claude/borg/runs` | Where drone sandboxes and run artifacts live. |
