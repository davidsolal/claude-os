-- Migration: Add Knowledge Lifecycle Tracking
-- Created: 2026-02-06
-- Purpose: Audit log for deduplication, consolidation, archival operations

CREATE TABLE IF NOT EXISTS lifecycle_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_name TEXT NOT NULL,
    operation_type TEXT NOT NULL,       -- dedup_scan, dedup_merge, consolidate, archive, restore
    status TEXT NOT NULL DEFAULT 'pending',
    input_doc_ids TEXT DEFAULT '[]',    -- JSON array
    output_doc_ids TEXT DEFAULT '[]',   -- JSON array
    details TEXT DEFAULT '{}',          -- JSON blob
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_kb ON lifecycle_log(kb_name);
CREATE INDEX IF NOT EXISTS idx_lifecycle_op ON lifecycle_log(operation_type);
