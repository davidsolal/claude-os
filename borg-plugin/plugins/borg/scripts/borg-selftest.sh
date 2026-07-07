#!/usr/bin/env bash
# borg-selftest.sh — end-to-end smoke test for the Borg pipeline.
#
# Runs borg-lib.sh, borg-drone.sh and borg-swarm.sh against a mock Claude OS
# API and a fake `ollama` shim — no server, no real models, no cost. Safe to
# run anywhere; everything happens in a temp dir.
#
# Usage: borg-selftest.sh
# Exit code: 0 = all checks passed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find a temp dir that allows execution (not noexec-mounted).
# /tmp is noexec on some Docker containers and hardened Linux setups, which
# breaks the fake ollama shim. Try TMPDIR, then /tmp, then fall back to
# a dir under the script's parent (usually the repo root or home).
_find_tmp() {
  local candidate
  for candidate in "${TMPDIR:-}" /tmp "${SCRIPT_DIR}/.selftest-tmp" "${HOME}/.cache/borg-selftest"; do
    [[ -z "$candidate" ]] && continue
    mkdir -p "$candidate" 2>/dev/null || continue
    local test_file="${candidate}/borg-exec-test.$$"
    printf '#!/bin/sh\necho ok\n' >"$test_file" 2>/dev/null && chmod +x "$test_file" 2>/dev/null
    if "$test_file" >/dev/null 2>&1; then
      rm -f "$test_file"
      mktemp -d "${candidate}/borg-selftest.XXXXXX" 2>/dev/null && return 0
    fi
    rm -f "$test_file" 2>/dev/null
  done
  return 1
}
TMP="$(_find_tmp)" || { echo "borg-selftest: cannot find an executable temp dir" >&2; exit 1; }
SERVER_PID=""
trap '[[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
check() {  # check <label> <rc>  (0 = ok)
  if [[ "$2" -eq 0 ]]; then PASS=$((PASS + 1)); echo "  ✅ $1"
  else FAIL=$((FAIL + 1)); echo "  ❌ $1"; fi
}

# ── Mock Claude OS API ───────────────────────────────────────────────────────
export MOCK_LOG="${TMP}/mock.log"
: >"$MOCK_LOG"
cat >"${TMP}/mock_api.py" <<'PYEOF'
import json, os, re, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = os.environ["MOCK_LOG"]
kbs = ["preexisting-kb"]
uploads = {}  # kb -> [filenames]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _log(self, line):
        with open(LOG, "a") as f: f.write(line + "\n")
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _auth(self):
        a = self.headers.get("Authorization")
        if a: self._log(f"AUTH {a}")
    def do_GET(self):
        self._auth()
        if self.path == "/health": self._json({"status": "ok"})
        elif self.path == "/api/kb": self._json({"knowledge_bases": [{"name": k} for k in kbs]})
        elif self.path.endswith("/documents"):
            kb = self.path.split("/")[3]
            self._json({"documents": [{"filename": f} for f in uploads.get(kb, [])]})
        else: self._json({"error": "not found"}, 404)
    def do_POST(self):
        self._auth()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/kb":
            name = json.loads(body)["name"]
            kbs.append(name); self._log(f"CREATE {name}")
            self._json({"ok": True})
        elif self.path == "/api/kb/search-all":
            self._json({"results": [{"kb_name": "borg-collective", "score": 0.9,
                                     "text": "prior knowledge: use JWT"}]})
        elif self.path.endswith("/upload"):
            kb = self.path.split("/")[3]
            m = re.search(rb'filename="([^"]+)"', body)
            fn = m.group(1).decode() if m else "?"
            uploads.setdefault(kb, []).append(fn)
            self._log(f"UPLOAD {kb} {fn}")
            self._json({"ok": True})
        elif self.path.endswith("/chat"):
            self._json({"answer": "mock answer"})
        else:
            self._json({"error": "not found"}, 404)

HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF

PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1]);s.close()')"
python3 "${TMP}/mock_api.py" "$PORT" &
SERVER_PID=$!
export CLAUDE_OS_API="http://127.0.0.1:${PORT}"
for _ in $(seq 1 50); do
  curl -sf -m 1 "${CLAUDE_OS_API}/health" >/dev/null 2>&1 && break
  sleep 0.1
done

# ── Fake ollama shim (behavior keyed on model name) ──────────────────────────
mkdir -p "${TMP}/bin"
cat >"${TMP}/bin/ollama" <<'SHEOF'
#!/usr/bin/env bash
cmd="${1:-}"; shift || true
if [[ "$cmd" == "ls" ]]; then
  printf 'NAME            SIZE\nwriter:mock     1B\nrecon:mock      1B\nsleepy:mock     1B\n'
  exit 0
fi
model=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) model="$2"; shift 2;;
    --) shift; break;;
    *) shift;;
  esac
done
case "$model" in
  writer:mock)
    printf '# Drone Report: mock\n**Status:** success\n\ndid the thing\n' >report.md
    echo "task complete";;
  recon:mock)
    echo "investigating..."
    printf '<<<BORG_REPORT>>>\n# Drone Report: recon\n**Status:** success\n\nfindings here\n<<<END_BORG_REPORT>>>\n';;
  sleepy:mock)
    sleep 60;;
esac
exit 0
SHEOF
chmod +x "${TMP}/bin/ollama"
export PATH="${TMP}/bin:${PATH}"
export BORG_RUNS_DIR="${TMP}/runs"

# ── 1. borg-lib primitives ───────────────────────────────────────────────────
echo "── borg-lib.sh"
# shellcheck source=borg-lib.sh
source "${SCRIPT_DIR}/borg-lib.sh"

borg_online; check "borg_online reaches the mock collective" $?

borg_ensure_kb "selftest-kb" "created by selftest" >/dev/null
grep -q "CREATE selftest-kb" "$MOCK_LOG"; check "borg_ensure_kb creates a missing KB" $?
borg_ensure_kb "preexisting-kb" >/dev/null; check "borg_ensure_kb is idempotent on an existing KB" $?

borg_recall "jwt strategy" | grep -q "prior knowledge: use JWT"
check "borg_recall returns hive passages" $?

BORG_AUTH_TOKEN="tok-123" borg_online
grep -q "AUTH Bearer tok-123" "$MOCK_LOG"
check "auth header arrives intact (no word-splitting)" $?

printf '# doc\n' >"${TMP}/doc.md"
borg_assimilate "borg-collective" "verify test" "${TMP}/doc.md" "verify-me.md" >/dev/null
borg_verify_doc "borg-collective" "verify-me.md"
check "borg_verify_doc confirms a landed document" $?
! borg_verify_doc "borg-collective" "never-uploaded.md"
check "borg_verify_doc rejects a missing document" $?

# ── 2. Single drone, happy path ──────────────────────────────────────────────
echo "── borg-drone.sh: worker"
OUT="$("${SCRIPT_DIR}/borg-drone.sh" --run-id st-run --name "d1/../evil" \
        --task "do the mock task" --model writer:mock --timeout 30 2>/dev/null)"
check "worker drone exits 0" $?
REPORT="$(printf '%s\n' "$OUT" | tail -n 1)"
[[ -f "$REPORT" ]] && grep -q "did the thing" "$REPORT"
check "drone wrote report.md in its sandbox" $?
grep -q "UPLOAD borg-collective st-run-d1-..-evil.md" "$MOCK_LOG"
check "report uploaded under a unique, sanitized filename" $?

# ── 3. Recon drone: report extracted from stdout markers ────────────────────
echo "── borg-drone.sh: recon (--readonly)"
OUT="$("${SCRIPT_DIR}/borg-drone.sh" --run-id st-run --name recon-1 \
        --task "investigate" --model recon:mock --readonly --timeout 30 2>/dev/null)"
check "recon drone exits 0" $?
REPORT="$(printf '%s\n' "$OUT" | tail -n 1)"
grep -q "findings here" "$REPORT" && ! grep -q "BORG_REPORT" "$REPORT"
check "recon report extracted from stdout markers" $?

# ── 4. Timeout watchdog ──────────────────────────────────────────────────────
echo "── borg-drone.sh: timeout"
"${SCRIPT_DIR}/borg-drone.sh" --run-id st-run --name slow-1 \
    --task "hang forever" --model sleepy:mock --timeout 2 >/dev/null 2>&1
[[ $? -eq 124 ]]; check "hung drone killed, rc=124" $?
grep -q "timed out after 2s" "${BORG_RUNS_DIR}/st-run/slow-1/report.md"
check "synthesized report records the timeout" $?

# ── 5. Swarm fan-out ─────────────────────────────────────────────────────────
echo "── borg-swarm.sh"
cat >"${TMP}/tasks.txt" <<'EOF'
# swarm selftest tasks
alpha :: first mock task
second mock task with no explicit name
EOF
SUMMARY="$("${SCRIPT_DIR}/borg-swarm.sh" --tasks "${TMP}/tasks.txt" \
            --parallel 2 --model writer:mock --run-id st-swarm --timeout 30 2>/dev/null)"
check "swarm exits 0 when all drones succeed" $?
SUMMARY="$(printf '%s\n' "$SUMMARY" | tail -n 1)"
[[ -f "$SUMMARY" ]] && grep -q "| alpha | 0 |" "$SUMMARY" && grep -q "| drone-2 | 0 |" "$SUMMARY"
check "swarm summary lists both drones with rc=0" $?
grep -q "UPLOAD borg-collective st-swarm-swarm-summary.md" "$MOCK_LOG"
check "swarm summary assimilated into the hive" $?

echo
echo "Borg selftest: ${PASS} passed, ${FAIL} failed."
[[ $FAIL -eq 0 ]]
