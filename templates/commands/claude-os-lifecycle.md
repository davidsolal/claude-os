---
name: claude-os-lifecycle
description: Knowledge base lifecycle management - dedup, consolidate, archive, and health analytics
---

# Claude OS Knowledge Lifecycle Management

Manage the health and lifecycle of your knowledge bases: find duplicates, consolidate related docs, archive stale content, and get health reports.

## Commands

```
/claude-os-lifecycle health [kb_name]         - Health report with recommendations
/claude-os-lifecycle dedup [kb_name]          - Scan and merge duplicate documents
/claude-os-lifecycle consolidate [kb_name]    - LLM-powered document merging
/claude-os-lifecycle archive [kb_name]        - Find stale docs, archive, restore
/claude-os-lifecycle logs [kb_name]           - View operation history
```

---

## HEALTH REPORT

### What Happens

```
/claude-os-lifecycle health my-project-project_memories
```

**Step 1: Get Health Report**
```
mcp__code-forge__kb_lifecycle_health
  kb_name: "my-project-project_memories"
```

**Step 2: Display Report**

```
═══════════════════════════════════════
KNOWLEDGE BASE HEALTH REPORT
═══════════════════════════════════════

KB: my-project-project_memories
Documents: 47 (42 with embeddings, 3 archived)
Last Updated: 2026-02-05

EMBEDDING COVERAGE:
  With embeddings: 42/47 (89.4%)
  Without: 5 docs need re-indexing

AGE DISTRIBUTION:
  Last 7 days: 8 docs
  Last 30 days: 15 docs
  Last 90 days: 12 docs
  Older: 12 docs

TOP SIMILAR PAIRS (potential duplicates):
  1. doc-a1b2 <-> doc-c3d4 (0.94 similarity)
  2. doc-e5f6 <-> doc-g7h8 (0.91 similarity)
  3. doc-i9j0 <-> doc-k1l2 (0.88 similarity)

RECOMMENDATIONS:
  [HIGH] Found 3 highly similar document pairs. Run dedup scan.
  [MEDIUM] 12 documents older than 90 days. Review for archival.
  [MEDIUM] 5 documents lack embeddings. Re-index recommended.

═══════════════════════════════════════
```

---

## DEDUPLICATION

### Scan for Duplicates

```
/claude-os-lifecycle dedup my-project-project_memories
```

**Step 1: Run Dedup Scan**
```
mcp__code-forge__kb_lifecycle_dedup
  kb_name: "my-project-project_memories"
  action: "scan"
  threshold: 0.85
```

**Step 2: Show Results**

```
═══════════════════════════════════════
DUPLICATE SCAN RESULTS
═══════════════════════════════════════

Scanned: 47 documents
Duplicate Density: 6.4%
Pairs Found: 3
Clusters: 2

CLUSTER 1 (3 docs):
  - doc-a1b2: "Authentication pattern using JWT tokens..."
  - doc-c3d4: "JWT authentication implementation pattern..."
  - doc-m3n4: "Token-based auth with JWT refresh..."
  Similarity: 0.91-0.94

CLUSTER 2 (2 docs):
  - doc-e5f6: "Redis caching strategy for API responses..."
  - doc-g7h8: "API response caching with Redis..."
  Similarity: 0.88

═══════════════════════════════════════
```

**Step 3: Ask User What to Do**

For each cluster, offer options:
- **Merge**: Keep the most complete doc, delete others
- **Consolidate**: Use LLM to merge into a single comprehensive doc
- **Skip**: Leave as-is

```
Cluster 1: What would you like to do?
[m] Merge (keep best, delete rest)
[c] Consolidate (LLM merge)
[s] Skip
```

**If merge:**
```
mcp__code-forge__kb_lifecycle_dedup
  kb_name: "my-project-project_memories"
  action: "merge"
  keep_doc_id: "doc-a1b2"
  remove_doc_ids: ["doc-c3d4", "doc-m3n4"]
```

**If consolidate:**
```
mcp__code-forge__kb_lifecycle_consolidate
  kb_name: "my-project-project_memories"
  doc_ids: ["doc-a1b2", "doc-c3d4", "doc-m3n4"]
  new_filename: "jwt-authentication-pattern-consolidated.md"
```

---

## CONSOLIDATION

### LLM-Powered Document Merging

```
/claude-os-lifecycle consolidate my-project-project_memories
```

**Step 1: Preview (dry run)**
```
mcp__code-forge__kb_lifecycle_consolidate
  kb_name: "my-project-project_memories"
  doc_ids: ["doc-a1b2", "doc-c3d4", "doc-m3n4"]
  new_filename: "consolidated-auth-patterns.md"
  dry_run: true
```

**Step 2: Show Preview**
```
═══════════════════════════════════════
CONSOLIDATION PREVIEW
═══════════════════════════════════════

Sources: 3 documents
Total content: 4,250 characters

Document 1: "Authentication pattern using JWT..."
Document 2: "JWT authentication implementation..."
Document 3: "Token-based auth with JWT refresh..."

This will:
  - Create: consolidated-auth-patterns.md
  - Delete: 3 source documents
  - Use LLM to merge content intelligently

Proceed? [y/n]
═══════════════════════════════════════
```

**Step 3: Execute**
```
mcp__code-forge__kb_lifecycle_consolidate
  kb_name: "my-project-project_memories"
  doc_ids: ["doc-a1b2", "doc-c3d4", "doc-m3n4"]
  new_filename: "consolidated-auth-patterns.md"
  dry_run: false
```

---

## ARCHIVAL

### Find and Archive Stale Documents

```
/claude-os-lifecycle archive my-project-project_memories
```

**Step 1: Find Stale Documents**
```
mcp__code-forge__kb_lifecycle_archive
  kb_name: "my-project-project_memories"
  action: "stale"
  stale_days: 90
```

**Step 2: Show Stale Documents**
```
═══════════════════════════════════════
STALE DOCUMENTS (>90 days old)
═══════════════════════════════════════

Found 5 stale documents:

1. old-pattern-123.md (142 days old)
2. debug-notes-456.md (128 days old)
3. meeting-notes-789.md (115 days old)
4. temp-workaround-012.md (103 days old)
5. initial-setup-345.md (98 days old)

[a] Archive all  [s] Select individually  [n] Skip
═══════════════════════════════════════
```

**Step 3: Archive Selected**
```
mcp__code-forge__kb_lifecycle_archive
  kb_name: "my-project-project_memories"
  action: "archive"
  doc_ids: ["old-pattern-123", "debug-notes-456"]
  reason: "stale - over 90 days"
```

### List Archived Documents

```
mcp__code-forge__kb_lifecycle_archive
  kb_name: "my-project-project_memories"
  action: "list"
```

### Restore Archived Documents

```
mcp__code-forge__kb_lifecycle_archive
  kb_name: "my-project-project_memories"
  action: "restore"
  doc_ids: ["old-pattern-123"]
```

---

## OPERATION LOGS

### View History

```
/claude-os-lifecycle logs my-project-project_memories
```

Shows recent lifecycle operations:

```
═══════════════════════════════════════
LIFECYCLE OPERATION LOG
═══════════════════════════════════════

1. [2026-02-06 10:30] dedup_scan - completed
   Found 3 duplicate pairs in 47 documents

2. [2026-02-06 10:32] dedup_merge - completed
   Kept doc-a1b2, removed 2 documents

3. [2026-02-06 10:35] archive - completed
   Archived 2 documents (reason: stale)

4. [2026-02-05 14:20] consolidate - completed
   Merged 3 documents into consolidated-patterns.md

═══════════════════════════════════════
```

---

## KB NAME RESOLUTION

If kb_name is not provided, use this strategy:
1. Check if working directory has a Claude OS project registered
2. If so, use `{project}-project_memories` as default
3. If not, list all KBs and ask user to pick one

```
mcp__code-forge__list_knowledge_bases
```

---

## WHY THIS MATTERS

- **Dedup**: Prevents memory bloat from saving similar insights repeatedly
- **Consolidate**: Turns fragmented notes into comprehensive references
- **Archive**: Keeps active memories relevant without permanent deletion
- **Health**: Proactive monitoring catches issues before they impact recall
- **Logs**: Full audit trail of all lifecycle operations
