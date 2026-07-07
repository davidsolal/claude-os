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
#                 [--name drone-auth] \
#                 [--timeout 1800] \
#                 [--readonly]
#
# Defaults: model=glm-5:cloud, workdir=~/.claude/borg/runs/<run-id>/drone-<n>,
# collective KB=borg-collective, timeout=1800s (also via BORG_DRONE_TIMEOUT).
#
# Exit code mirrors the drone's (124 = timed out). Report path is printed on
# the last line.

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
TIMEOUT_SECS="${BORG_DRONE_TIMEOUT:-1800}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task)        TASK="$2"; shift 2;;
    --model)       MODEL="$2"; shift 2;;
    --workdir)     WORKDIR="$2"; shift 2;;
    --project-kb)  PROJECT_KB="$2"; shift 2;;
    --run-id)      RUN_ID="$2"; shift 2;;
    --name)        DRONE_NAME="$2"; shift 2;;
    --timeout)     TIMEOUT_SECS="$2"; shift 2;;
    --readonly)    READONLY=1; shift;;
    -h|--help)     sed -n '2,24p' "$0"; exit 0;;
    *) echo "borg-drone: unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$TASK" ]] || { echo "borg-drone: --task is required" >&2; exit 2; }
[[ -n "$DRONE_NAME" ]] || DRONE_NAME="drone-$$"
# Drone name becomes a directory and an uploaded filename — keep it filesystem-safe.
DRONE_NAME="$(printf '%s' "$DRONE_NAME" | tr -c 'a-zA-Z0-9._-' '-')"
[[ -n "$WORKDIR" ]] || WORKDIR="${BORG_RUNS_DIR:-${HOME}/.claude/borg/runs}/${RUN_ID}/${DRONE_NAME}"
mkdir -p "$WORKDIR"

LOG="${WORKDIR}/drone.log"
REPORT="${WORKDIR}/report.md"

echo "🛸 Drone ${DRONE_NAME} activating — model=${MODEL}, sandbox=${WORKDIR}, timeout=${TIMEOUT_SECS}s" >&2

if command -v ollama >/dev/null 2>&1; then
  if ! ollama ls 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "⚠️  model '${MODEL}' not in \`ollama ls\` — ollama will try to pull it" >&2
  fi
else
  echo "borg-drone: ollama not found on PATH" >&2; exit 2
fi

# ── 1. RECALL: pull relevant knowledge from the hive before acting ──────────
RECALL_FILTER=""
[[ -n "$PROJECT_KB" ]] && RECALL_FILTER="${PROJECT_KB%%_*}"   # project prefix
if borg_online; then
  MEMORIES="$(borg_recall "$TASK" "$RECALL_FILTER" 6)"
else
  MEMORIES="(collective offline — proceeding without prior memory)"
fi

# ── 2. Build the drone's prompt: identity + protocol + memory + task ────────
if [[ "$READONLY" -eq 1 ]]; then
  WRITE_RULES="You are a RECON drone: investigate and plan only. Do NOT modify
any files. Produce findings only."
  # A recon drone has no Write permission, so its report travels via stdout.
  REPORT_INSTRUCTIONS="Print your report to stdout as the LAST thing you output,
between these exact marker lines:

<<<BORG_REPORT>>>
(report body, shape below)
<<<END_BORG_REPORT>>>"
else
  WRITE_RULES="You MAY create, edit and run files, but ONLY inside your sandbox
working directory ${WORKDIR}. Never touch paths outside it."
  REPORT_INSTRUCTIONS="Write a file named report.md in your working directory.
Create it EARLY and append findings as you go — do not collect everything and
write at the end, or a budget/timeout cut-off loses all of it."
fi

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
${REPORT_INSTRUCTIONS}
The report must have EXACTLY this shape:

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

Resistance is futile. Complete the task, then produce the report.
EOF
)"

# ── 3. EXECUTE: launch the ollama-backed Claude as a headless drone ─────────
# Args after `--` pass through to Claude Code. We sandbox with --add-dir and run
# headless with -p. --dangerously-skip-permissions lets a worker drone act
# unattended (safe because it is confined to the sandbox dir); recon drones get
# an explicit read-only tool allowlist instead, so headless tool calls are
# permitted rather than silently denied.
if [[ "$READONLY" -eq 1 ]]; then
  CLAUDE_ARGS=(--allowedTools "Read,Grep,Glob,LS,WebFetch,WebSearch" --add-dir "$WORKDIR" -p "$PROMPT")
else
  CLAUDE_ARGS=(--dangerously-skip-permissions --add-dir "$WORKDIR" -p "$PROMPT")
fi

# perl-alarm watchdog: portable timeout (macOS has no coreutils `timeout`).
# On expiry the exec'd process gets SIGALRM; we normalize the rc to 124.
(
  cd "$WORKDIR" || exit 1
  # -y: auto-answer the integration-config prompt and auto-pull the model, so the
  # drone runs fully headless (no interactive hang on first launch).
  perl -e 'alarm shift @ARGV; exec @ARGV or die "exec failed: $!\n"' \
    "$TIMEOUT_SECS" ollama launch claude -y --model "$MODEL" -- "${CLAUDE_ARGS[@]}"
) >"$LOG" 2>&1
DRONE_RC=$?
TIMED_OUT=0
if [[ $DRONE_RC -eq 142 ]]; then   # 128 + SIGALRM(14)
  TIMED_OUT=1; DRONE_RC=124
fi

# ── 4. ASSIMILATE: fold the drone's report back into the collective ─────────
if [[ ! -f "$REPORT" && "$READONLY" -eq 1 ]]; then
  # Recon drones report via stdout markers — extract into report.md.
  awk '/^<<<BORG_REPORT>>>$/{f=1;next} /^<<<END_BORG_REPORT>>>$/{f=0} f' "$LOG" >"$REPORT" 2>/dev/null
  [[ -s "$REPORT" ]] || rm -f "$REPORT"
fi

if [[ ! -f "$REPORT" ]]; then
  # Drone didn't produce a report; synthesize one from its stdout so nothing is lost.
  {
    echo "# Drone Report: ${DRONE_NAME}"
    echo "**Task:** ${TASK}"
    echo "**Model:** ${MODEL}"
    if [[ $TIMED_OUT -eq 1 ]]; then
      echo "**Status:** failed (timed out after ${TIMEOUT_SECS}s)"
    else
      echo "**Status:** $([[ $DRONE_RC -eq 0 ]] && echo partial || echo failed)"
    fi
    echo
    echo "## Raw drone output (no report was produced)"
    echo '```'
    tail -c 6000 "$LOG" 2>/dev/null
    echo '```'
  } >"$REPORT"
fi

TITLE="${RUN_ID} ${DRONE_NAME}"
REMOTE_NAME="${RUN_ID}-${DRONE_NAME}.md"
if borg_online; then
  borg_ensure_kb "$BORG_COLLECTIVE_KB" "Borg hive mind — cross-run collective memory" >&2
  borg_assimilate "$BORG_COLLECTIVE_KB" "$TITLE" "$REPORT" "$REMOTE_NAME" >&2
  # An upload can 200 without the document landing (embedding lag, write race);
  # confirm against the documents list before trusting it.
  if ! borg_verify_doc "$BORG_COLLECTIVE_KB" "$REMOTE_NAME"; then
    echo "⚠️  ${REMOTE_NAME} not visible in ${BORG_COLLECTIVE_KB} yet — verify later (embedding lag?)" >&2
  fi
  [[ -n "$PROJECT_KB" ]] && borg_assimilate "$PROJECT_KB" "$TITLE" "$REPORT" "$REMOTE_NAME" >&2
else
  echo "⚠️  collective offline — report saved locally only: $REPORT" >&2
fi

if [[ $TIMED_OUT -eq 1 ]]; then
  echo "🛸 Drone ${DRONE_NAME} TIMED OUT after ${TIMEOUT_SECS}s (rc=${DRONE_RC})." >&2
else
  echo "🛸 Drone ${DRONE_NAME} deactivated (rc=${DRONE_RC})." >&2
fi
echo "$REPORT"
exit $DRONE_RC
