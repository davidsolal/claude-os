---
name: claude-os-session
description: Lightweight session management with automatic context loading
---

# Claude OS Session Management

Simple session tracking that loads context on start and saves learnings on end.

## Setup

The state file is **project-local**: `{cwd}/claude-os-state.json` (where `{cwd}` is the current working directory).

## Commands

```
/claude-os-session start [task]     - Start session with context loading
/claude-os-session end              - End session with save prompts
/claude-os-session status           - Current session status
/claude-os-session save [note]      - Quick save during session
/claude-os-session blocker [desc]   - Track blocker
/claude-os-session pattern [desc]   - Document pattern discovered
```

---

## START SESSION

```
/claude-os-session start "redesign appointment dashboard"
```

**Step 1: Read State**

Read `{cwd}/claude-os-state.json`. If it exists and has content, show:

```
Last time: [one_liner]
Branch: [last_branch]
Stopped: [stopped_at]
```

If the file doesn't exist, that's fine — skip to Step 2.

**Step 2: Search Memories**

```
mcp__code-forge__search_knowledge_base
  kb_name: {project}-project_memories
  query: "[task] recent"
```

Show the top 3-5 results as key context.

**Step 3: Git Context**

```bash
git branch --show-current
git status --short
git log -3 --oneline
```

**Step 4: Ready**

Show a brief summary and start working:

```
Session: [task]
Branch: [current-branch]
Context: [N] memories loaded
[One-line summary of each relevant memory]

Ready to go.
```

**Step 5: Update State**

Write to `{cwd}/claude-os-state.json`:

```json
{
  "last_task": "[task]",
  "last_branch": "[current-branch]",
  "stopped_at": "[current ISO timestamp]",
  "one_liner": ""
}
```

---

## END SESSION

```
/claude-os-session end
```

**Step 1: Summarize Work**

```bash
git diff --stat HEAD~3..HEAD
git log --oneline -5
```

Show what was accomplished (files changed, commits made).

**Step 2: Offer to Save**

Ask: "Anything worth remembering? I can save decisions, patterns, or blockers."

If yes, use `/claude-os-remember` to save each item.

**Step 3: Write State**

Ask for a one-liner summary (or generate one from the work), then write:

```json
{
  "last_task": "[task from start]",
  "last_branch": "[current-branch]",
  "stopped_at": "[current ISO timestamp]",
  "one_liner": "[one-liner summary of where we stopped]"
}
```

---

## DURING SESSION

### Quick Save

```
/claude-os-session save "Found fix for N+1 query in appointments"
```

Immediately saves to memories with session context using `/claude-os-remember`.

### Track Blocker

```
/claude-os-session blocker "Tekmetric API returning 500 on appointment sync"
```

Saves a blocker to memories and searches for related solutions:

```
mcp__code-forge__search_knowledge_base
  kb_name: {project}-project_memories
  query: "[blocker description]"
```

### Document Pattern

```
/claude-os-session pattern "Service objects return model on success, error string on fail"
```

Saves pattern to memories with current task context.

---

## STATUS CHECK

```
/claude-os-session status
```

Read `{cwd}/claude-os-state.json` and show:

```
Task: [last_task]
Branch: [last_branch]
Last active: [stopped_at]
Summary: [one_liner]
```

---

## STATE FILE FORMAT

`{cwd}/claude-os-state.json` — exactly 4 fields:

```json
{
  "last_task": "Fix appointment email flood",
  "last_branch": "fix-email-flood",
  "stopped_at": "2026-02-06T18:30:00Z",
  "one_liner": "Fixed the dedup check, still need to add rate limiting"
}
```

That's it. No statistics, no preferences, no flags. If the file doesn't exist, create it on session end.

---

## EXAMPLES

**Starting work:**
```
You: /claude-os-session start "add user notifications"
Me: Last time: "Finished auth flow, need to add email templates" (2 days ago)
    Found 3 memories about notification systems...
    Ready to implement!
```

**Mid-session save:**
```
You: /claude-os-session save "Use Sidekiq for long-running notifications"
Me: Saved to memories with session context.
```

**Hitting a blocker:**
```
You: /claude-os-session blocker "Redis connection timing out"
Me: Tracked. Found a similar issue from Oct 15... [shows solution]
```

**Ending session:**
```
You: /claude-os-session end
Me: 3 commits, 8 files changed.
    Anything worth remembering?
You: Yeah, the notification batch pattern
Me: Saved! State updated. See you next time.
```
