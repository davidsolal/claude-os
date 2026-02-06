"""
Tests for Knowledge Lifecycle Engine.
Covers deduplication, archival, analytics, and consolidation.
"""

import pytest
import tempfile
import json
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import numpy as np

from app.core.sqlite_manager import SQLiteManager
from app.core.kb_types import KBType


@pytest.fixture
def lifecycle_db():
    """Create a clean SQLite database with lifecycle support."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
        temp_db_path = temp_file.name

    manager = SQLiteManager(temp_db_path)
    yield manager

    if Path(temp_db_path).exists():
        Path(temp_db_path).unlink()


@pytest.fixture
def populated_kb(lifecycle_db):
    """Create a KB with sample documents and embeddings."""
    kb_name = "test-lifecycle-kb"
    lifecycle_db.create_collection(kb_name, KBType.GENERIC, description="Test KB")

    # Create documents with embeddings
    # Doc 1 and 2 are near-duplicates (similar embeddings)
    # Doc 3 is different
    np.random.seed(42)
    base_vec = np.random.randn(768).astype(np.float32)
    base_vec = base_vec / np.linalg.norm(base_vec)

    # Near-duplicate of base (very small perturbation for high similarity)
    similar_vec = base_vec + np.random.randn(768).astype(np.float32) * 0.01
    similar_vec = similar_vec / np.linalg.norm(similar_vec)

    # Different vector
    different_vec = np.random.randn(768).astype(np.float32)
    different_vec = different_vec / np.linalg.norm(different_vec)

    docs = ["Authentication using JWT tokens for API security",
            "JWT token authentication pattern for securing APIs",
            "Redis caching strategy for database query optimization"]
    embeddings = [base_vec.tolist(), similar_vec.tolist(), different_vec.tolist()]
    metadatas = [
        {"filename": "auth-jwt.md", "upload_date": datetime.now().isoformat()},
        {"filename": "jwt-auth.md", "upload_date": datetime.now().isoformat()},
        {"filename": "redis-cache.md", "upload_date": (datetime.now() - timedelta(days=120)).isoformat()}
    ]
    ids = ["doc-auth-1", "doc-auth-2", "doc-redis-1"]

    lifecycle_db.add_documents(kb_name, docs, embeddings, metadatas, ids)

    return kb_name, lifecycle_db


@pytest.fixture
def engine(populated_kb):
    """Create a KnowledgeLifecycleEngine with populated KB."""
    kb_name, db = populated_kb

    # Patch the singleton to use our test DB
    with patch("app.core.knowledge_lifecycle.get_sqlite_manager", return_value=db):
        from app.core.knowledge_lifecycle import KnowledgeLifecycleEngine
        e = KnowledgeLifecycleEngine()
        e.db = db
        yield e, kb_name, db


@pytest.mark.unit
class TestDeduplication:
    """Test duplicate scanning and merging."""

    def test_scan_empty_kb(self, lifecycle_db):
        """Scan with no documents returns empty results."""
        kb_name = "empty-kb"
        lifecycle_db.create_collection(kb_name, KBType.GENERIC)

        with patch("app.core.knowledge_lifecycle.get_sqlite_manager", return_value=lifecycle_db):
            from app.core.knowledge_lifecycle import KnowledgeLifecycleEngine
            engine = KnowledgeLifecycleEngine()
            engine.db = lifecycle_db
            result = engine.scan_duplicates(kb_name)

        assert result["total_documents"] == 0
        assert result["duplicate_pairs"] == []
        assert result["clusters"] == []
        assert result["duplicate_density"] == 0.0

    def test_identical_vectors_detected(self, engine):
        """Near-duplicate documents are detected above threshold."""
        e, kb_name, _ = engine
        result = e.scan_duplicates(kb_name, threshold=0.85)

        assert result["total_documents"] == 3
        assert len(result["duplicate_pairs"]) >= 1

        # The auth docs should be paired
        pair = result["duplicate_pairs"][0]
        doc_ids = {pair["doc_a"], pair["doc_b"]}
        assert "doc-auth-1" in doc_ids or "doc-auth-2" in doc_ids
        assert pair["similarity"] >= 0.85

    def test_below_threshold_ignored(self, engine):
        """Documents below threshold are not flagged."""
        e, kb_name, _ = engine
        result = e.scan_duplicates(kb_name, threshold=0.99)

        # At 0.99 threshold, only truly identical docs match
        # Our similar_vec has noise so shouldn't match at 0.99
        for pair in result["duplicate_pairs"]:
            assert pair["similarity"] >= 0.99

    def test_union_find_clustering(self, engine):
        """Duplicate pairs are clustered correctly."""
        e, kb_name, _ = engine
        result = e.scan_duplicates(kb_name, threshold=0.85)

        if result["clusters"]:
            cluster = result["clusters"][0]
            assert "cluster_id" in cluster
            assert "doc_ids" in cluster
            assert cluster["size"] >= 2

    def test_merge_dry_run(self, engine):
        """Dry run merge previews without deleting."""
        e, kb_name, db = engine
        result = e.merge_duplicates(
            kb_name, keep_doc_id="doc-auth-1",
            remove_doc_ids=["doc-auth-2"], dry_run=True
        )

        assert result["dry_run"] is True
        assert result["keep_doc_id"] == "doc-auth-1"
        assert "doc-auth-2" in result["would_remove"]

        # Verify doc still exists
        docs = db.get_all_embeddings(kb_name)
        doc_ids = [d["doc_id"] for d in docs]
        assert "doc-auth-2" in doc_ids

    def test_merge_actual(self, engine):
        """Actual merge deletes duplicate documents."""
        e, kb_name, db = engine
        result = e.merge_duplicates(
            kb_name, keep_doc_id="doc-auth-1",
            remove_doc_ids=["doc-auth-2"], dry_run=False
        )

        assert result["dry_run"] is False
        assert result["deleted_count"] == 1

        # Verify doc was deleted
        docs = db.get_all_embeddings(kb_name)
        doc_ids = [d["doc_id"] for d in docs]
        assert "doc-auth-1" in doc_ids
        assert "doc-auth-2" not in doc_ids


@pytest.mark.unit
class TestArchival:
    """Test document archival and restoration."""

    def test_archive_sets_flag(self, engine):
        """Archive sets archived flag in metadata."""
        e, kb_name, db = engine
        result = e.archive_documents(kb_name, ["doc-auth-1"], reason="test")

        assert result["archived_count"] == 1

        docs = db.get_all_embeddings(kb_name, exclude_archived=False)
        doc = next(d for d in docs if d["doc_id"] == "doc-auth-1")
        assert doc["metadata"]["archived"] is True
        assert doc["metadata"]["archive_reason"] == "test"
        assert "archived_at" in doc["metadata"]

    def test_restore_clears_flag(self, engine):
        """Restore clears archived flag."""
        e, kb_name, db = engine

        # Archive first
        e.archive_documents(kb_name, ["doc-auth-1"])

        # Then restore
        result = e.restore_documents(kb_name, ["doc-auth-1"])
        assert result["restored_count"] == 1

        docs = db.get_all_embeddings(kb_name, exclude_archived=False)
        doc = next(d for d in docs if d["doc_id"] == "doc-auth-1")
        assert doc["metadata"].get("archived") is False

    def test_exclude_archived_from_embeddings(self, engine):
        """Archived docs are excluded when exclude_archived=True."""
        e, kb_name, db = engine

        e.archive_documents(kb_name, ["doc-auth-1"])

        # With exclude
        docs_filtered = db.get_all_embeddings(kb_name, exclude_archived=True)
        doc_ids_filtered = [d["doc_id"] for d in docs_filtered]
        assert "doc-auth-1" not in doc_ids_filtered

        # Without exclude
        docs_all = db.get_all_embeddings(kb_name, exclude_archived=False)
        doc_ids_all = [d["doc_id"] for d in docs_all]
        assert "doc-auth-1" in doc_ids_all

    def test_find_stale(self, engine):
        """Find stale documents correctly identifies old docs."""
        e, kb_name, _ = engine

        with patch("app.core.knowledge_lifecycle.get_documents_metadata") as mock_docs:
            mock_docs.return_value = [
                {"filename": "old.md", "upload_date": (datetime.now() - timedelta(days=120)).isoformat()},
                {"filename": "new.md", "upload_date": datetime.now().isoformat()}
            ]
            result = e.find_stale_documents(kb_name, stale_days=90)

        assert result["stale_count"] == 1
        assert result["stale_documents"][0]["filename"] == "old.md"
        assert result["stale_documents"][0]["age_days"] >= 119

    def test_list_archived(self, engine):
        """List archived returns only archived docs."""
        e, kb_name, _ = engine

        # Archive one doc
        e.archive_documents(kb_name, ["doc-redis-1"], reason="stale")

        result = e.list_archived(kb_name)
        assert result["archived_count"] == 1
        assert result["archived_documents"][0]["doc_id"] == "doc-redis-1"
        assert result["archived_documents"][0]["archive_reason"] == "stale"


@pytest.mark.unit
class TestAnalytics:
    """Test health reports and growth timeline."""

    def test_health_report_structure(self, engine):
        """Health report contains all expected sections."""
        e, kb_name, _ = engine

        with patch("app.core.knowledge_lifecycle.get_collection_stats") as mock_stats, \
             patch("app.core.knowledge_lifecycle.get_documents_metadata") as mock_docs:
            mock_stats.return_value = {
                "total_documents": 3, "total_chunks": 3,
                "last_updated": datetime.now().isoformat()
            }
            mock_docs.return_value = [
                {"filename": "a.md", "upload_date": datetime.now().isoformat()},
                {"filename": "b.md", "upload_date": datetime.now().isoformat()},
                {"filename": "c.md", "upload_date": (datetime.now() - timedelta(days=100)).isoformat()}
            ]

            report = e.get_health_report(kb_name)

        assert "kb_name" in report
        assert "stats" in report
        assert "embedding_coverage" in report
        assert "top_similar_pairs" in report
        assert "age_distribution" in report
        assert "recommendations" in report
        assert report["embedding_coverage"]["total_docs"] == 3
        assert report["embedding_coverage"]["with_embeddings"] == 3

    def test_growth_timeline_grouping(self, engine):
        """Growth timeline groups documents by period."""
        e, kb_name, _ = engine

        with patch("app.core.knowledge_lifecycle.get_documents_metadata") as mock_docs:
            now = datetime.now()
            mock_docs.return_value = [
                {"filename": "a.md", "upload_date": now.isoformat()},
                {"filename": "b.md", "upload_date": now.isoformat()},
                {"filename": "c.md", "upload_date": (now - timedelta(days=35)).isoformat()}
            ]

            result = e.get_growth_timeline(kb_name, granularity="month")

        assert result["kb_name"] == kb_name
        assert result["granularity"] == "month"
        assert result["total_documents"] == 3
        assert len(result["timeline"]) >= 1

        # Last entry should have cumulative total = 3
        assert result["timeline"][-1]["total"] == 3

    def test_density_calculation(self, engine):
        """Duplicate density is calculated correctly."""
        e, kb_name, _ = engine
        result = e.scan_duplicates(kb_name, threshold=0.85)

        # density = pairs / (n*(n-1)/2) where n=3 => 3 possible pairs
        assert 0 <= result["duplicate_density"] <= 1.0


@pytest.mark.unit
class TestLifecycleLog:
    """Test lifecycle audit logging."""

    def test_insert_and_query_log(self, lifecycle_db):
        """Insert and retrieve lifecycle logs."""
        kb_name = "test-log-kb"

        log_id = lifecycle_db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="dedup_scan",
            status="completed",
            input_doc_ids=["doc-1", "doc-2"],
            details={"pairs_found": 5}
        )

        assert log_id > 0

        logs = lifecycle_db.get_lifecycle_logs(kb_name)
        assert len(logs) == 1
        assert logs[0]["operation_type"] == "dedup_scan"
        assert logs[0]["status"] == "completed"
        assert logs[0]["input_doc_ids"] == ["doc-1", "doc-2"]
        assert logs[0]["details"]["pairs_found"] == 5

    def test_update_log_status(self, lifecycle_db):
        """Update lifecycle log status and completion."""
        log_id = lifecycle_db.insert_lifecycle_log(
            kb_name="test-kb",
            operation_type="consolidate",
            status="pending"
        )

        success = lifecycle_db.update_lifecycle_log(
            log_id, "completed",
            output_doc_ids=["new-doc-1"],
            details={"merged": 3}
        )

        assert success

        logs = lifecycle_db.get_lifecycle_logs("test-kb")
        assert logs[0]["status"] == "completed"
        assert logs[0]["output_doc_ids"] == ["new-doc-1"]
        assert logs[0]["completed_at"] is not None

    def test_filter_by_operation_type(self, lifecycle_db):
        """Filter logs by operation type."""
        lifecycle_db.insert_lifecycle_log("kb1", "dedup_scan", "completed")
        lifecycle_db.insert_lifecycle_log("kb1", "archive", "completed")
        lifecycle_db.insert_lifecycle_log("kb1", "dedup_merge", "completed")

        dedup_logs = lifecycle_db.get_lifecycle_logs("kb1", operation_type="dedup_scan")
        assert len(dedup_logs) == 1

        all_logs = lifecycle_db.get_lifecycle_logs("kb1")
        assert len(all_logs) == 3


@pytest.mark.unit
class TestSQLiteManagerLifecycle:
    """Test the new SQLiteManager methods for lifecycle operations."""

    def test_get_all_embeddings(self, lifecycle_db):
        """get_all_embeddings returns docs with parsed embeddings."""
        kb_name = "test-emb-kb"
        lifecycle_db.create_collection(kb_name, KBType.GENERIC)

        vec = np.random.randn(768).astype(np.float32)
        lifecycle_db.add_documents(
            kb_name,
            documents=["Test content"],
            embeddings=[vec.tolist()],
            metadatas=[{"filename": "test.md"}],
            ids=["doc-1"]
        )

        results = lifecycle_db.get_all_embeddings(kb_name)
        assert len(results) == 1
        assert results[0]["doc_id"] == "doc-1"
        assert results[0]["embedding"] is not None
        assert len(results[0]["embedding"]) == 768

    def test_update_document_metadata(self, lifecycle_db):
        """update_document_metadata merges new keys into existing metadata."""
        kb_name = "test-meta-kb"
        lifecycle_db.create_collection(kb_name, KBType.GENERIC)

        vec = np.random.randn(768).astype(np.float32)
        lifecycle_db.add_documents(
            kb_name,
            documents=["Test content"],
            embeddings=[vec.tolist()],
            metadatas=[{"filename": "test.md", "tags": ["a"]}],
            ids=["doc-1"]
        )

        success = lifecycle_db.update_document_metadata(
            kb_name, "doc-1", {"archived": True, "new_key": "value"}
        )
        assert success

        docs = lifecycle_db.get_all_embeddings(kb_name, exclude_archived=False)
        doc = docs[0]
        assert doc["metadata"]["archived"] is True
        assert doc["metadata"]["new_key"] == "value"
        assert doc["metadata"]["filename"] == "test.md"  # original preserved

    def test_delete_documents_by_ids(self, lifecycle_db):
        """delete_documents_by_ids removes specified documents."""
        kb_name = "test-delete-kb"
        lifecycle_db.create_collection(kb_name, KBType.GENERIC)

        vec = np.random.randn(768).astype(np.float32)
        lifecycle_db.add_documents(
            kb_name,
            documents=["Doc 1", "Doc 2", "Doc 3"],
            embeddings=[vec.tolist(), vec.tolist(), vec.tolist()],
            metadatas=[{"filename": "1.md"}, {"filename": "2.md"}, {"filename": "3.md"}],
            ids=["doc-1", "doc-2", "doc-3"]
        )

        deleted = lifecycle_db.delete_documents_by_ids(kb_name, ["doc-1", "doc-3"])
        assert deleted == 2

        remaining = lifecycle_db.get_all_embeddings(kb_name)
        assert len(remaining) == 1
        assert remaining[0]["doc_id"] == "doc-2"


@pytest.mark.integration
class TestConsolidation:
    """Integration tests for consolidation (requires Ollama)."""

    def test_consolidate_dry_run(self, engine):
        """Dry run shows preview without making changes."""
        e, kb_name, db = engine

        result = e.consolidate_related(
            kb_name,
            doc_ids=["doc-auth-1", "doc-auth-2"],
            new_filename="consolidated-auth.md",
            dry_run=True
        )

        assert result["dry_run"] is True
        assert result["source_count"] == 2
        assert result["total_chars"] > 0

        # Verify originals still exist
        docs = db.get_all_embeddings(kb_name)
        doc_ids = [d["doc_id"] for d in docs]
        assert "doc-auth-1" in doc_ids
        assert "doc-auth-2" in doc_ids

    def test_consolidate_insufficient_docs(self, engine):
        """Consolidation with <2 docs returns error."""
        e, kb_name, _ = engine

        result = e.consolidate_related(
            kb_name,
            doc_ids=["doc-auth-1"],
            new_filename="single.md"
        )

        assert "error" in result
