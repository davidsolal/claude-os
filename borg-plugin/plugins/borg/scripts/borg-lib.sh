#!/usr/bin/env bash
# borg-lib.sh — shared collective-memory primitives for the Borg.
#
# "We are the Borg. Your distinctiveness will be added to our own."
#
# Every drone (internal Claude subagent OR external ollama-launched Claude)
# touches the SAME Claude OS knowledge bases through these helpers, so the
# whole swarm behaves as one mind:
#
#   borg_recall      <query> [kb_filter]     -> search the hive (cross-KB)
#   borg_recall_kb   <kb> <query>            -> RAG-query one KB
#   borg_assimilate  <kb> <title> <file.md>  -> write knowledge back to the hive
#   borg_ensure_kb   <name> <description>    -> create a KB if it does not exist
#
# Source it:  source "$(dirname "$0")/borg-lib.sh"
#
# Auth is disabled by default in Claude OS (no CLAUDE_OS_EMAIL), so plain curl
# works. If you enable auth, export BORG_AUTH_TOKEN and it will be sent as a
# Bearer token.

BORG_API="${CLAUDE_OS_API:-http://localhost:8051}"
BORG_COLLECTIVE_KB="${BORG_COLLECTIVE_KB:-borg-collective}"

_borg_auth_header() {
  if [[ -n "${BORG_AUTH_TOKEN:-}" ]]; then
    printf 'Authorization: Bearer %s' "$BORG_AUTH_TOKEN"
  fi
}

# Is the collective reachable?
borg_online() {
  curl -sf -m 5 "${BORG_API}/health" >/dev/null 2>&1
}

# Create a KB if it is not already present. Idempotent.
borg_ensure_kb() {
  local name="$1" desc="${2:-Borg knowledge base}"
  if curl -sf -m 10 "${BORG_API}/api/kb" \
      | python3 -c "import sys,json;n='$name';sys.exit(0 if any(k['name']==n for k in json.load(sys.stdin)['knowledge_bases']) else 1)" 2>/dev/null; then
    return 0
  fi
  curl -s -m 30 -X POST "${BORG_API}/api/kb" \
    -H "Content-Type: application/json" \
    $( [[ -n "${BORG_AUTH_TOKEN:-}" ]] && printf -- '-H %s' "$(_borg_auth_header)" ) \
    -d "$(python3 -c "import json,sys;print(json.dumps({'name':'$name','kb_type':'generic','description':sys.argv[1]}))" "$desc")" \
    >/dev/null
}

# Cross-KB semantic search. Returns concatenated matching passages.
#   borg_recall "auth token refresh strategy"            # all KBs
#   borg_recall "auth token refresh strategy" "myapp-"   # scope by prefix
borg_recall() {
  local query="$1" kb_filter="${2:-}" top_k="${3:-8}"
  local body
  body=$(python3 -c "import json,sys; d={'query':sys.argv[1],'top_k':int(sys.argv[3])}; f=sys.argv[2]; d.update({'kb_filter':f} if f else {}); print(json.dumps(d))" \
    "$query" "$kb_filter" "$top_k")
  curl -s -m 60 -X POST "${BORG_API}/api/kb/search-all" \
    -H "Content-Type: application/json" \
    $( [[ -n "${BORG_AUTH_TOKEN:-}" ]] && printf -- '-H %s' "$(_borg_auth_header)" ) \
    -d "$body" \
  | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('(collective unreachable)'); sys.exit(0)
res = d.get('results', [])
if not res:
    print('(no prior knowledge in the collective)'); sys.exit(0)
for r in res:
    print(f\"### [{r.get('kb_name','?')}] score={r.get('score',0):.3f}\")
    print(r.get('text','').strip()[:1200])
    print()
"
}

# RAG-query a single KB (uses the LLM to answer over that KB).
borg_recall_kb() {
  local kb="$1" query="$2"
  curl -s -m 90 -X POST "${BORG_API}/api/kb/${kb}/chat" \
    -H "Content-Type: application/json" \
    $( [[ -n "${BORG_AUTH_TOKEN:-}" ]] && printf -- '-H %s' "$(_borg_auth_header)" ) \
    -d "$(python3 -c "import json,sys;print(json.dumps({'query':sys.argv[1]}))" "$query")" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('answer') or d.get('response') or json.dumps(d)[:1500])" 2>/dev/null \
    || echo "(query failed)"
}

# Write a markdown file into a KB. The file IS the memory.
#   borg_assimilate borg-collective "Drone 3 report" /tmp/report.md
borg_assimilate() {
  local kb="$1" title="$2" file="$3"
  [[ -f "$file" ]] || { echo "borg_assimilate: no such file: $file" >&2; return 1; }
  curl -s -m 60 -X POST "${BORG_API}/api/kb/${kb}/upload" \
    $( [[ -n "${BORG_AUTH_TOKEN:-}" ]] && printf -- '-H %s' "$(_borg_auth_header)" ) \
    -F "file=@${file}" \
    -w "\n" >/dev/null \
    && echo "🧠 assimilated into ${kb}: ${title}"
}
