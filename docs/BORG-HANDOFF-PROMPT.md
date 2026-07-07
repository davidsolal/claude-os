# Borg handoff — two lineages, three repos

You are picking up work on "the Borg", a Queen/drone orchestration system whose
shared memory is a Claude OS server (`http://localhost:8051`). It exists in TWO
lineages that share one API contract and one protocol (RECALL → EXECUTE →
ASSIMILATE). Your job: understand the current state, keep the invariants, and
continue work without breaking either lineage.

## The repos and how to get them

1. **claude-os** (shell/Claude-Code lineage) — local at
   `~/Tree/Documents/Dev/claude-os`. Two remotes:
   - `origin` = `https://github.com/brobertsaz/claude-os` — the upstream
     author's repo, `push: false`. Pull upstream updates from here, NEVER push.
   - `fork` = `git@github.com:davidsolal/claude-os.git` — the user's fork
     (created 2026-07-07). `main` and `borg-improvements` are pushed here;
     all borg history is merged into `main` (tip `ad9bbc7`).
   ```bash
   # on the user's machine:
   cd ~/Tree/Documents/Dev/claude-os && git checkout main && git pull fork main
   # on any other machine:
   gh repo clone davidsolal/claude-os
   ```
2. **hermes-borg-skill** (Hermes-native lineage, skill + references) and
3. **hermes-borg-plugin** (Hermes-native lineage, python toolset) — both under
   the user's GitHub account (proprietary license), already cloned:
   ```bash
   cd ~/Tree/Documents/Dev
   ls hermes-borg-skill hermes-borg-plugin || {
     gh repo clone davidsolal/hermes-borg-skill
     gh repo clone davidsolal/hermes-borg-plugin
   }
   git -C hermes-borg-skill pull; git -C hermes-borg-plugin pull   # main = truth
   ```

## Changelog — claude-os `main` (== `borg-improvements`, 2026-07-07)

`main` was fast-forwarded over the borg branch and pushed to the fork.
Commits on top of upstream `main` (ee7b62b):

- **cdf5c3b — baseline.** First commit of the previously-untracked Borg:
  Queen agent (`templates/agents/borg-queen.md`), `/borg` command
  (`templates/commands/borg.md`), `scripts/borg-lib.sh` (curl primitives),
  `scripts/borg-drone.sh` (headless ollama-backed Claude drone launcher),
  `docs/BORG.md`, the `borg-plugin/` marketplace bundle, and installer symlinks
  for agents in `setup-claude-os.sh`.
- **96168b2 — hardening + reconciliation with the Hermes lineage.**
  - Bug fixes: project KB filter now actually passed to `borg_recall`; Bearer
    auth header no longer word-split (auth mode was entirely broken);
    shell→python injection removed from `borg_ensure_kb`; uploads fail loudly
    (`curl -f`) instead of silently losing reports; `borg-collective` KB is
    ensured before upload; reports upload as unique `<run-id>-<drone>.md`
    (previously every drone uploaded a colliding `report.md`); drone names
    sanitized; `--readonly` drones get a read-only tool allowlist and report
    via `<<<BORG_REPORT>>>` stdout markers that the launcher extracts
    (previously headless tool calls were denied and a no-write drone was told
    to write a file).
  - New: `scripts/borg-swarm.sh` (task-list fan-out, bounded parallelism,
    summary table assimilated to the hive); perl-alarm timeout watchdog per
    drone (`--timeout`/`BORG_DRONE_TIMEOUT`, default 1800s, rc=124);
    `BORG_RUNS_DIR` override; `scripts/borg-selftest.sh` — 17-check end-to-end
    test against a mock API + fake `ollama` (free, hermetic).
  - Reconciled from Hermes: `borg_verify_doc` (an assimilate can 200 without
    the document landing — verify via `GET /api/kb/{kb}/documents`; the drone
    launcher auto-verifies); KB API paths take the KB **name** (underscored),
    never the slug; drone prompts require writing reports EARLY and appending
    (tool-budget cut-off pitfall); "drone reports are self-reports" rule in the
    Queen/command docs; lineage section in `docs/BORG.md`.
- **0c1fe6d — this handoff prompt** (`docs/BORG-HANDOFF-PROMPT.md`).
- **ad9bbc7 — unrelated pending work committed alongside**: PHP tag extraction
  (classes/interfaces/traits/enums/functions/methods) in
  `app/core/tree_sitter_indexer.py`, and `.env.example` default
  `OLLAMA_MODEL=gemma4:latest`.

## Changelog — hermes repos (state of `main`)

- **hermes-borg-plugin**: single commit `3a846f7` (2026-07-02). Python plugin
  exposing `borg_status`, `borg_ensure_kb`, `borg_recall`, `borg_recall_kb`,
  `borg_assimilate` to the Hermes Agent. Drone launching is NOT here — it's
  Hermes-native `delegate_task` (the legacy detached drone tool was removed).
  Note: it assimilates via `POST /api/kb/{kb}/documents/content` (JSON),
  whereas the shell lib uses multipart `/upload`. The Claude OS server
  supports both.
- **hermes-borg-skill**: `7e74b7e` initial skill (Jul 2) → `3fc4c93` adds
  `/borg cycle-auto` autonomous multi-hour cron builds (Jul 2) → `30457b0`
  adds 50+ session references (Jul 7) → `c59e035` SKILL.md update (Jul 7).
  `SKILL.md` is the Queen's full operating procedure: drone model tiering
  (trivial/cheap/standard/strong via `drone_model.py`; NEVER run drones on the
  premium/main model), checkpointing (`borg_checkpoint.py`), 429 routing,
  crash recovery, and a large pitfalls list. `references/` holds ~180
  battle-tested case studies — search here BEFORE re-solving any drone problem.

## How to work

- **Before and after touching any borg shell script**, run
  `bash scripts/borg-selftest.sh` in claude-os — it must pass 17/17. Extend it
  when you add behavior.
- **Keep the mirror in sync**: `scripts/borg-*.sh` are canonical; after edits,
  `cp` them into `borg-plugin/plugins/borg/scripts/` and apply equivalent
  wording changes to the plugin's agent/command markdown. (`borg-plugin.zip`
  at repo root is a stale build artifact — rebuild or ignore.)
- **Invariants (both lineages)**: KB name not slug; verify every assimilation
  against the documents list; unique report filenames; drones work in
  sandboxes and the Queen reviews before applying (drone reports are
  self-reports — re-read claimed edits, run tests); drones write reports early.
- **Git**: commit as `devsolal@gmail.com`. claude-os work goes to `main` and is
  pushed with `git push fork main` (the `fork` remote = davidsolal/claude-os);
  `origin` is upstream and rejects pushes. Hermes repos push to their `main`.
- **Deliberately NOT committed** in the claude-os working tree:
  `.claude/settings.local.json` (local permissions) and untracked runtime junk
  (`data/`, `dump.rdb`, `sessions/`, `hts-cache/`, stale `borg-plugin.zip`).
  Leave them alone or gitignore them; do not sweep them into commits.
- **Open cross-pollination items**: port `borg_verify_doc` into
  hermes-borg-plugin's python (it currently trusts the assimilate response);
  consider porting Hermes checkpointing and model tiering to the shell borg;
  optionally switch the shell lib to the `/documents/content` endpoint for
  parity.
