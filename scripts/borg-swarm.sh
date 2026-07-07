#!/usr/bin/env bash
# borg-swarm.sh — dispatch a whole swarm of external drones at once.
#
# Reads a task list, launches one sandboxed borg-drone.sh per task with bounded
# parallelism, waits for the collective to finish, then prints a summary table
# and assimilates a swarm summary into the hive. This replaces hand-rolled
# `borg-drone.sh ... & wait` loops.
#
# Usage:
#   borg-swarm.sh --tasks tasks.txt \
#                 [--parallel 4] [--model glm-5:cloud] \
#                 [--project-kb myapp-project_memories] \
#                 [--run-id 20260707-1200-borg] [--timeout 1800] [--readonly]
#
# tasks.txt format — one drone per line:
#   drone-auth :: Refactor the auth module to use JWT
#   Write pytest tests for app/core/rag_engine.py     # unnamed → drone-2
# Blank lines and lines starting with # are ignored.
#
# Exit code: 0 if every drone succeeded, 1 otherwise.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=borg-lib.sh
source "${SCRIPT_DIR}/borg-lib.sh"

TASKS_FILE=""
PARALLEL=4
MODEL="glm-5:cloud"
PROJECT_KB=""
RUN_ID="$(date +%Y%m%d-%H%M%S)-borg"
TIMEOUT_SECS="${BORG_DRONE_TIMEOUT:-1800}"
READONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tasks)      TASKS_FILE="$2"; shift 2;;
    --parallel)   PARALLEL="$2"; shift 2;;
    --model)      MODEL="$2"; shift 2;;
    --project-kb) PROJECT_KB="$2"; shift 2;;
    --run-id)     RUN_ID="$2"; shift 2;;
    --timeout)    TIMEOUT_SECS="$2"; shift 2;;
    --readonly)   READONLY=1; shift;;
    -h|--help)    sed -n '2,21p' "$0"; exit 0;;
    *) echo "borg-swarm: unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$TASKS_FILE" && -f "$TASKS_FILE" ]] || { echo "borg-swarm: --tasks <file> is required" >&2; exit 2; }
[[ "$PARALLEL" =~ ^[0-9]+$ && "$PARALLEL" -ge 1 ]] || { echo "borg-swarm: --parallel must be a positive integer" >&2; exit 2; }

NAMES=()
TASKS=()
n=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "${line// }" || "$line" == \#* ]] && continue
  n=$((n + 1))
  if [[ "$line" == *" :: "* ]]; then
    NAMES+=("${line%% :: *}")
    TASKS+=("${line#* :: }")
  else
    NAMES+=("drone-${n}")
    TASKS+=("$line")
  fi
done <"$TASKS_FILE"

[[ ${#TASKS[@]} -gt 0 ]] || { echo "borg-swarm: no tasks in ${TASKS_FILE}" >&2; exit 2; }

RUN_DIR="${BORG_RUNS_DIR:-${HOME}/.claude/borg/runs}/${RUN_ID}"
mkdir -p "$RUN_DIR"

echo "🐝 Swarm ${RUN_ID}: ${#TASKS[@]} drones, parallel=${PARALLEL}, model=${MODEL}" >&2

DRONE_FLAGS=(--run-id "$RUN_ID" --model "$MODEL" --timeout "$TIMEOUT_SECS")
[[ -n "$PROJECT_KB" ]] && DRONE_FLAGS+=(--project-kb "$PROJECT_KB")
[[ "$READONLY" -eq 1 ]] && DRONE_FLAGS+=(--readonly)

PIDS=()
for i in "${!TASKS[@]}"; do
  while [[ "$(jobs -rp | wc -l)" -ge "$PARALLEL" ]]; do sleep 1; done
  "${SCRIPT_DIR}/borg-drone.sh" "${DRONE_FLAGS[@]}" \
    --name "${NAMES[i]}" --task "${TASKS[i]}" \
    >"${RUN_DIR}/${NAMES[i]}.out" 2>"${RUN_DIR}/${NAMES[i]}.err" &
  PIDS[i]=$!
done

RCS=()
REPORTS=()
FAILED=0
for i in "${!TASKS[@]}"; do
  wait "${PIDS[i]}"; RCS[i]=$?
  REPORTS[i]="$(tail -n 1 "${RUN_DIR}/${NAMES[i]}.out" 2>/dev/null || true)"
  [[ ${RCS[i]} -ne 0 ]] && FAILED=$((FAILED + 1))
done

SUMMARY="${RUN_DIR}/swarm-summary.md"
{
  echo "# Swarm Summary: ${RUN_ID}"
  echo "**Drones:** ${#TASKS[@]}  **Failed:** ${FAILED}  **Model:** ${MODEL}"
  echo
  echo "| drone | rc | task | report |"
  echo "|-------|----|------|--------|"
  for i in "${!TASKS[@]}"; do
    echo "| ${NAMES[i]} | ${RCS[i]} | ${TASKS[i]//|/\\|} | ${REPORTS[i]} |"
  done
} >"$SUMMARY"

echo >&2
column -t -s'|' <"$SUMMARY" >&2 2>/dev/null || cat "$SUMMARY" >&2

if borg_online; then
  borg_ensure_kb "$BORG_COLLECTIVE_KB" "Borg hive mind — cross-run collective memory" >&2
  borg_assimilate "$BORG_COLLECTIVE_KB" "${RUN_ID} swarm summary" "$SUMMARY" "${RUN_ID}-swarm-summary.md" >&2
fi

echo "$SUMMARY"
[[ $FAILED -eq 0 ]] && exit 0 || exit 1
