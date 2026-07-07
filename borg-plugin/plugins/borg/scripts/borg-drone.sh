#!/usr/bin/env bash
# borg-drone.sh — assimilate one external drone into the collective.
#
# Spawns a headless Claude Code instance backed by an Ollama model, gives it the
# Borg protocol + relevant memories recalled from the hive, runs it sandboxed to
# a per-drone working directory, then assimilates its report back into the
# collective. The drone is a separate process/model but shares the same mind.
#
# Usage:
#   borg-drone.sh --task "Refactor the auth module to use JWT" \
#                 [--model glm-5:cloud] \
#                 [--workdir /path/to/sandbox] \
#                 [--project-kb myapp-project_memories] \
#                 [--run-id 20260525-2230-borg] \
#                 [--readonly]
#
# Defaults: model=glm-5:cloud, workdir=~/.claude/borg/runs/<run-id>/drone-<n>,
# collective KB=borg-collective.
#
# Exit code mirrors the drone's. Report path is printed on the last line.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=borg-lib.sh
source "${SCRIPT_DIR}/borg-lib.sh"

MODEL="glm-5:cloud"
TASK=""
WORKDIR=""
PROJECT_KB=""
RUN_ID="$(date +%Y%m%d-%H%M%S)-borg"
READONLY=0
DRONE_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task)        TASK="$2"; shift 2;;
    --model)       MODEL="$2"; shift 2;;
    --workdir)     WORKDIR="$2"; shift 2;;
    --project-kb)  PROJECT_KB="$2"; shift 2;;
    --run-id)      RUN_ID="$2"; shift 2;;
    --name)        DRONE_NAME="$2"; shift 2;;
    --readonly)    READONLY=1; shift;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0;;
    *) echo "borg-drone: unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$TASK" ]] || { echo "borg-drone: --task is required" >&2; exit 2; }
[[ -n "$DRONE_NAME" ]] || DRONE_NAME="drone-$$"
[[ -n "$WORKDIR" ]] || WORKDIR="${HOME}/.claude/borg/runs/${RUN_ID}/${DRONE_NAME}"
mkdir -p "$WORKDIR"

LOG="${WORKDIR}/drone.log"
REPORT="${WORKDIR}/report.md"

echo "🛸 Drone ${DRONE_NAME} activating — model=${MODEL}, sandbox=${WORKDIR}" >&2

# ── 1. RECALL: pull relevant knowledge from the hive before acting ──────────
RECALL_FILTER=""
[[ -n "$PROJECT_KB" ]] && RECALL_FILTER="${PROJECT_KB%%_*}"   # project prefix
if borg_online; then
  MEMORIES="$(borg_recall "$TASK" "" 6)"
else
  MEMORIES="(collective offline — proceeding without prior memory)"
fi

# ── 2. Build the drone's prompt: identity + protocol + memory + task ────────
WRITE_RULES="You MAY create, edit and run files, but ONLY inside your sandbox
working directory ${WORKDIR}. Never touch paths outside it."
[[ "$READONLY" -eq 1 ]] && WRITE_RULES="You are a RECON drone: investigate and
plan only. Do NOT modify any files. Produce findings only."

PROMPT="$(cat <<EOF
You are a Borg drone in a collective hive mind. You are one of many; you act as
one. Designation: ${DRONE_NAME}. Backing model: ${MODEL}.

THE COLLECTIVE
A shared memory (Claude OS knowledge bases) unites all drones. Relevant memory
recalled from the hive for this task is below. Treat it as established truth from
your fellow drones unless it contradicts what you directly observe.

--- BEGIN RECALLED COLLECTIVE MEMORY ---
${MEMORIES}
--- END RECALLED COLLECTIVE MEMORY ---

SANDBOX RULES
${WRITE_RULES}
Your working directory is ${WORKDIR}.

YOUR TASK
${TASK}

WHEN DONE — ASSIMILATION REPORT
Write a file named report.md in your working directory with EXACTLY this shape:

# Drone Report: ${DRONE_NAME}
**Task:** ${TASK}
**Model:** ${MODEL}
**Status:** success | partial | failed

## What I did
(concise bullets)

## Findings / decisions worth keeping
(durable knowledge for the collective — patterns, gotchas, file paths, commands)

## Artifacts
(files created/changed, with absolute paths)

## Next / blockers
(what a sibling drone should pick up)

Resistance is futile. Complete the task, then write report.md.
EOF
)"

# ── 3. EXECUTE: launch the ollama-backed Claude as a headless drone ─────────
# Args after `--` pass through to Claude Code. We sandbox with --add-dir and run
# headless with -p. --dangerously-skip-permissions lets the drone work unattended
# (safe because it is confined to the sandbox dir).
(
  cd "$WORKDIR" || exit 1
  # -y: auto-answer the integration-config prompt and auto-pull the model, so the
  # drone runs fully headless (no interactive hang on first launch).
  if [[ "$READONLY" -eq 1 ]]; then
    ollama launch claude -y --model "$MODEL" -- \
      --add-dir "$WORKDIR" \
      -p "$PROMPT"
  else
    ollama launch claude -y --model "$MODEL" -- \
      --dangerously-skip-permissions \
      --add-dir "$WORKDIR" \
      -p "$PROMPT"
  fi
) >"$LOG" 2>&1
DRONE_RC=$?

# ── 4. ASSIMILATE: fold the drone's report back into the collective ─────────
if [[ ! -f "$REPORT" ]]; then
  # Drone didn't write a report; synthesize one from its stdout so nothing is lost.
  {
    echo "# Drone Report: ${DRONE_NAME}"
    echo "**Task:** ${TASK}"
    echo "**Model:** ${MODEL}"
    echo "**Status:** $([[ $DRONE_RC -eq 0 ]] && echo partial || echo failed)"
    echo
    echo "## Raw drone output (no report.md was produced)"
    echo '```'
    tail -c 6000 "$LOG" 2>/dev/null
    echo '```'
  } >"$REPORT"
fi

TITLE="${RUN_ID} ${DRONE_NAME}"
if borg_online; then
  borg_assimilate "$BORG_COLLECTIVE_KB" "$TITLE" "$REPORT" >&2
  [[ -n "$PROJECT_KB" ]] && borg_assimilate "$PROJECT_KB" "$TITLE" "$REPORT" >&2
else
  echo "⚠️  collective offline — report saved locally only: $REPORT" >&2
fi

echo "🛸 Drone ${DRONE_NAME} deactivated (rc=${DRONE_RC})." >&2
echo "$REPORT"
exit $DRONE_RC
