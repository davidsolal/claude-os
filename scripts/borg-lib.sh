#!/usr/bin/env bash
# borg-lib.sh — shared collective-memory primitives for the Borg.
#
# "We are the Borg. Your distinctiveness will be added to our own."
#
# Every drone (internal Claude subagent OR external ollama-launched Claude)
# touches the SAME Claude OS knowledge bases through these helpers, so the
# whole swarm behaves as one mind:
#
#   borg_recall      <query> [kb_filter] [top_k]        -> search the hive (cross-KB)
#   borg_recall_kb   <kb> <query>                       -> RAG-query one KB
#   borg_assimilate  <kb> <title> <file.md> [remote]    -> write knowledge back to the hive
#   borg_verify_doc  <kb> <filename>                    -> confirm a document landed in a KB
#   borg_ensure_kb   <name> [description]               -> create a KB if it does not exist
#
# KB identifiers: endpoints take the KB *name* (underscored form from
# GET /api/kb), never the slug — a lesson inherited from the Hermes borg lineage.
#
# Source it:  source "$(dirname "$0")/borg-lib.sh"
#
# Auth is disabled by default in Claude OS (no CLAUDE_OS_EMAIL), so plain curl
# works. If you enable auth, export BORG_AUTH_TOKEN and it will be sent as a
# Bearer token.

BORG_API="${CLAUDE_OS_API:-http://localhost:8051}"
BORG_COLLECTIVE_KB="${BORG_COLLECTIVE_KB:-borg-collective}"

# curl with the Bearer header passed as real argv — command-substituting the
# header into the curl line word-splits "Authorization: Bearer x" apart.
_borg_curl() {
  if [[ -n "${BORG_AUTH_TOKEN:-}" ]]; then
    curl -H "Authorization: Bearer ${BORG_AUTH_TOKEN}" "$@"
  else
    curl "$@"
  fi
}

# Is the collective reachable?
borg_online() {
  _borg_curl -sf -m 5 "${BORG_API}/health" >/dev/null 2>&1
}

# Create a KB if it is not already present. Idempotent.
borg_ensure_kb() {
  local name="$1" desc="${2:-Borg knowledge base}"
  if _borg_curl -sf -m 10 "${BORG_API}/api/kb" \
      | python3 -c "import sys,json;n=sys.argv[1];sys.exit(0 if any(k['name']==n for k in json.load(sys.stdin).get('knowledge_bases',[])) else 1)" "$name" 2>/dev/null; then
    return 0
  fi
  _borg_curl -sf -m 30 -X POST "${BORG_API}/api/kb" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,sys;print(json.dumps({'name':sys.argv[1],'kb_type':'generic','description':sys.argv[2]}))" "$name" "$desc")" \
    >/dev/null \
    || { echo "borg_ensure_kb: failed to create KB '${name}'" >&2; return 1; }
}

# Cross-KB semantic search. Returns concatenated matching passages.
#   borg_recall "auth token refresh strategy"            # all KBs
#   borg_recall "auth token refresh strategy" "myapp-"   # scope by prefix
borg_recall() {
  local query="$1" kb_filter="${2:-}" top_k="${3:-8}"
  local body
  body=$(python3 -c "import json,sys; d={'query':sys.argv[1],'top_k':int(sys.argv[3])}; f=sys.argv[2]; d.update({'kb_filter':f} if f else {}); print(json.dumps(d))" \
    "$query" "$kb_filter" "$top_k")
  _borg_curl -s -m 60 -X POST "${BORG_API}/api/kb/search-all" \
    -H "Content-Type: application/json" \
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
  _borg_curl -s -m 90 -X POST "${BORG_API}/api/kb/${kb}/chat" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,sys;print(json.dumps({'query':sys.argv[1]}))" "$query")" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('answer') or d.get('response') or json.dumps(d)[:1500])" 2>/dev/null \
    || echo "(query failed)"
}

# Write a markdown file into a KB. The file IS the memory.
# Pass a unique remote filename — the KB keys documents by filename, so two
# drones both uploading "report.md" would collide.
#   borg_assimilate borg-collective "Drone 3 report" /tmp/report.md 20260707-drone-3.md
borg_assimilate() {
  local kb="$1" title="$2" file="$3" remote="${4:-}"
  [[ -f "$file" ]] || { echo "borg_assimilate: no such file: $file" >&2; return 1; }
  [[ -n "$remote" ]] || remote="$(basename "$file")"
  if _borg_curl -sf -m 60 -X POST "${BORG_API}/api/kb/${kb}/upload" \
      -F "file=@${file};filename=${remote}" >/dev/null; then
    echo "🧠 assimilated into ${kb}: ${title}"
  else
    echo "⚠️  borg_assimilate: upload to '${kb}' FAILED (${title}); kept locally: ${file}" >&2
    return 1
  fi
}

# Confirm a document actually landed in a KB. An upload can 200 while the
# document never appears (embedding lag, DB write race) — hard-won Hermes-borg
# pitfall: never trust the assimilate response alone.
#   borg_verify_doc borg-collective 20260707-drone-3.md
borg_verify_doc() {
  local kb="$1" filename="$2"
  _borg_curl -sf -m 15 "${BORG_API}/api/kb/${kb}/documents" \
  | python3 -c "
import sys, json
target = sys.argv[1]
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
docs = d if isinstance(d, list) else d.get('documents', [])
names = { (x.get('filename') or x.get('name') or '') if isinstance(x, dict) else str(x) for x in docs }
sys.exit(0 if target in names else 1)
" "$filename"
}
