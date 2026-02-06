"""
Knowledge Lifecycle Engine for Claude OS.
Handles deduplication, consolidation, analytics, and archival of KB memories.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests

from app.core.sqlite_manager import get_sqlite_manager
from app.core.kb_metadata import get_collection_stats, get_documents_metadata
from app.core.config import Config

logger = logging.getLogger(__name__)


class KnowledgeLifecycleEngine:
    """Manages the lifecycle of knowledge base documents."""

    def __init__(self):
        self.db = get_sqlite_manager()

    # ========================================================================
    # DEDUPLICATION
    # ========================================================================

    def scan_duplicates(
        self,
        kb_name: str,
        threshold: float = 0.85,
        max_pairs: int = 100
    ) -> Dict[str, Any]:
        """
        Scan a KB for duplicate/near-duplicate documents using embedding similarity.

        Returns duplicate pairs, clusters, and density score.
        """
        docs = self.db.get_all_embeddings(kb_name, exclude_archived=True)

        # Filter to docs that have embeddings
        docs_with_emb = [d for d in docs if d["embedding"] is not None]
        total_docs = len(docs_with_emb)

        if total_docs < 2:
            return {
                "total_documents": total_docs,
                "duplicate_pairs": [],
                "clusters": [],
                "duplicate_density": 0.0,
                "message": "Not enough documents for comparison"
            }

        # Pre-normalize all vectors for efficient cosine similarity via dot product
        embeddings = np.array([d["embedding"] for d in docs_with_emb])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1  # avoid division by zero
        normalized = embeddings / norms

        # Compute pairwise similarities (upper triangle only)
        duplicate_pairs = []
        for i in range(total_docs):
            if len(duplicate_pairs) >= max_pairs:
                break
            for j in range(i + 1, total_docs):
                if len(duplicate_pairs) >= max_pairs:
                    break
                similarity = float(np.dot(normalized[i], normalized[j]))
                if similarity >= threshold:
                    duplicate_pairs.append({
                        "doc_a": docs_with_emb[i]["doc_id"],
                        "doc_b": docs_with_emb[j]["doc_id"],
                        "similarity": round(similarity, 4),
                        "content_a_preview": docs_with_emb[i]["content"][:200],
                        "content_b_preview": docs_with_emb[j]["content"][:200]
                    })

        # Sort by similarity descending
        duplicate_pairs.sort(key=lambda x: x["similarity"], reverse=True)

        # Cluster duplicates using union-find
        clusters = self._cluster_duplicates(
            [(p["doc_a"], p["doc_b"], p["similarity"]) for p in duplicate_pairs]
        )

        # Calculate duplicate density
        total_possible = total_docs * (total_docs - 1) / 2
        density = len(duplicate_pairs) / total_possible if total_possible > 0 else 0.0

        # Log the operation
        self.db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="dedup_scan",
            status="completed",
            details={
                "total_documents": total_docs,
                "threshold": threshold,
                "pairs_found": len(duplicate_pairs),
                "clusters_found": len(clusters),
                "density": round(density, 4)
            }
        )

        return {
            "total_documents": total_docs,
            "duplicate_pairs": duplicate_pairs,
            "clusters": clusters,
            "duplicate_density": round(density, 4)
        }

    def _cluster_duplicates(
        self, pairs: List[Tuple[str, str, float]]
    ) -> List[Dict[str, Any]]:
        """Group duplicate pairs into clusters using union-find."""
        parent = {}

        def find(x):
            if x not in parent:
                parent[x] = x
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for doc_a, doc_b, _ in pairs:
            union(doc_a, doc_b)

        # Group by root
        groups = defaultdict(list)
        all_docs = set()
        for doc_a, doc_b, _ in pairs:
            all_docs.add(doc_a)
            all_docs.add(doc_b)

        for doc_id in all_docs:
            groups[find(doc_id)].append(doc_id)

        clusters = []
        for root, members in groups.items():
            if len(members) > 1:
                clusters.append({
                    "cluster_id": root,
                    "doc_ids": sorted(set(members)),
                    "size": len(set(members))
                })

        return clusters

    def merge_duplicates(
        self,
        kb_name: str,
        keep_doc_id: str,
        remove_doc_ids: List[str],
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Merge duplicates by keeping one document and deleting the rest.

        Args:
            kb_name: Knowledge base name
            keep_doc_id: Document ID to keep
            remove_doc_ids: Document IDs to remove
            dry_run: If True, preview only without making changes
        """
        log_id = self.db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="dedup_merge",
            status="pending",
            input_doc_ids=[keep_doc_id] + remove_doc_ids,
            details={"dry_run": dry_run, "keep_doc_id": keep_doc_id}
        )

        if dry_run:
            self.db.update_lifecycle_log(
                log_id, "completed",
                details={"dry_run": True, "would_remove": remove_doc_ids}
            )
            return {
                "dry_run": True,
                "keep_doc_id": keep_doc_id,
                "would_remove": remove_doc_ids,
                "remove_count": len(remove_doc_ids)
            }

        deleted_count = self.db.delete_documents_by_ids(kb_name, remove_doc_ids)

        self.db.update_lifecycle_log(
            log_id, "completed",
            output_doc_ids=[keep_doc_id],
            details={"deleted_count": deleted_count}
        )

        return {
            "dry_run": False,
            "keep_doc_id": keep_doc_id,
            "removed": remove_doc_ids,
            "deleted_count": deleted_count
        }

    # ========================================================================
    # CONSOLIDATION
    # ========================================================================

    def consolidate_related(
        self,
        kb_name: str,
        doc_ids: List[str],
        new_filename: str,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Consolidate multiple related documents into a single merged document
        using LLM-powered summarization.
        """
        # Fetch source documents
        all_docs = self.db.get_all_embeddings(kb_name, exclude_archived=False)
        doc_map = {d["doc_id"]: d for d in all_docs}

        source_docs = []
        for doc_id in doc_ids:
            if doc_id in doc_map:
                source_docs.append(doc_map[doc_id])

        if len(source_docs) < 2:
            return {"error": "Need at least 2 documents to consolidate"}

        source_texts = [d["content"] for d in source_docs]

        if dry_run:
            return {
                "dry_run": True,
                "source_doc_ids": doc_ids,
                "source_count": len(source_docs),
                "total_chars": sum(len(t) for t in source_texts),
                "previews": [t[:200] for t in source_texts]
            }

        log_id = self.db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="consolidate",
            status="running",
            input_doc_ids=doc_ids,
            details={"new_filename": new_filename}
        )

        # Generate consolidated content via LLM
        consolidated_text = self._generate_consolidation(source_texts, kb_name)

        if not consolidated_text:
            self.db.update_lifecycle_log(
                log_id, "failed",
                details={"error": "LLM consolidation failed"}
            )
            return {"error": "Failed to generate consolidated content"}

        # Generate embedding for the new document
        try:
            embed_response = requests.post(
                f"{Config.OLLAMA_HOST}/api/embed",
                json={"model": Config.OLLAMA_EMBED_MODEL, "input": consolidated_text},
                timeout=30
            )
            embed_response.raise_for_status()
            embedding = embed_response.json().get("embeddings", [[]])[0]
        except Exception as e:
            logger.error(f"Failed to embed consolidated doc: {e}")
            embedding = None

        # Build metadata with provenance
        metadata = {
            "filename": new_filename,
            "type": "text/markdown",
            "consolidated_from": doc_ids,
            "consolidation_date": datetime.now().isoformat(),
            "upload_date": datetime.now().isoformat()
        }

        # Insert the new document
        import uuid
        new_doc_id = f"consolidated-{uuid.uuid4().hex[:8]}"

        if embedding:
            self.db.add_documents(
                kb_name=kb_name,
                documents=[consolidated_text],
                embeddings=[embedding],
                metadatas=[metadata],
                ids=[new_doc_id]
            )

        # Delete the source documents
        deleted = self.db.delete_documents_by_ids(kb_name, doc_ids)

        self.db.update_lifecycle_log(
            log_id, "completed",
            output_doc_ids=[new_doc_id],
            details={
                "new_doc_id": new_doc_id,
                "sources_deleted": deleted,
                "consolidated_length": len(consolidated_text)
            }
        )

        return {
            "new_doc_id": new_doc_id,
            "new_filename": new_filename,
            "sources_consolidated": len(source_docs),
            "sources_deleted": deleted,
            "consolidated_length": len(consolidated_text),
            "preview": consolidated_text[:500]
        }

    def _generate_consolidation(self, source_texts: List[str], kb_name: str) -> Optional[str]:
        """Generate a consolidated summary using Ollama LLM."""
        combined = "\n\n---\n\n".join(
            f"**Document {i+1}:**\n{text}" for i, text in enumerate(source_texts)
        )

        prompt = f"""You are consolidating related knowledge base documents into a single comprehensive document.

Knowledge base: {kb_name}

Below are {len(source_texts)} related documents. Create a single consolidated document that:
1. Preserves all unique information from each source
2. Eliminates redundancy
3. Organizes information logically
4. Uses clear markdown formatting

Source documents:

{combined}

Write the consolidated document:"""

        try:
            response = requests.post(
                f"{Config.OLLAMA_HOST}/api/generate",
                json={
                    "model": Config.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 2048
                    }
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as e:
            logger.error(f"LLM consolidation failed: {e}")
            return None

    # ========================================================================
    # ANALYTICS
    # ========================================================================

    def get_health_report(
        self,
        kb_name: str,
        include_top_similar: bool = True,
        top_similar_count: int = 10
    ) -> Dict[str, Any]:
        """Generate a comprehensive health report for a knowledge base."""
        # Basic stats
        stats = get_collection_stats(kb_name)
        docs_metadata = get_documents_metadata(kb_name)

        report = {
            "kb_name": kb_name,
            "stats": stats,
            "document_count": stats.get("total_documents", 0),
            "chunk_count": stats.get("total_chunks", 0),
            "last_updated": stats.get("last_updated"),
        }

        # Embedding coverage
        all_docs = self.db.get_all_embeddings(kb_name, exclude_archived=False)
        total = len(all_docs)
        with_embeddings = sum(1 for d in all_docs if d["embedding"] is not None)
        archived = sum(1 for d in all_docs if d["metadata"].get("archived"))

        report["embedding_coverage"] = {
            "total_docs": total,
            "with_embeddings": with_embeddings,
            "without_embeddings": total - with_embeddings,
            "coverage_pct": round(with_embeddings / total * 100, 1) if total > 0 else 0
        }
        report["archived_count"] = archived

        # Top similar pairs (for duplicate detection preview)
        if include_top_similar and with_embeddings >= 2:
            docs_with_emb = [d for d in all_docs if d["embedding"] is not None and not d["metadata"].get("archived")]
            if len(docs_with_emb) >= 2:
                embeddings = np.array([d["embedding"] for d in docs_with_emb])
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                norms[norms == 0] = 1
                normalized = embeddings / norms

                top_pairs = []
                for i in range(len(docs_with_emb)):
                    for j in range(i + 1, len(docs_with_emb)):
                        sim = float(np.dot(normalized[i], normalized[j]))
                        if sim > 0.7:
                            top_pairs.append({
                                "doc_a": docs_with_emb[i]["doc_id"],
                                "doc_b": docs_with_emb[j]["doc_id"],
                                "similarity": round(sim, 4)
                            })

                top_pairs.sort(key=lambda x: x["similarity"], reverse=True)
                report["top_similar_pairs"] = top_pairs[:top_similar_count]
            else:
                report["top_similar_pairs"] = []
        else:
            report["top_similar_pairs"] = []

        # Document age distribution
        age_distribution = {"last_7_days": 0, "last_30_days": 0, "last_90_days": 0, "older": 0}
        now = datetime.now()
        for doc in docs_metadata:
            upload_date_str = doc.get("upload_date", "")
            if upload_date_str:
                try:
                    upload_date = datetime.fromisoformat(upload_date_str)
                    age = now - upload_date
                    if age <= timedelta(days=7):
                        age_distribution["last_7_days"] += 1
                    elif age <= timedelta(days=30):
                        age_distribution["last_30_days"] += 1
                    elif age <= timedelta(days=90):
                        age_distribution["last_90_days"] += 1
                    else:
                        age_distribution["older"] += 1
                except (ValueError, TypeError):
                    age_distribution["older"] += 1

        report["age_distribution"] = age_distribution

        # Recent lifecycle operations
        recent_ops = self.db.get_lifecycle_logs(kb_name, limit=5)
        report["recent_operations"] = recent_ops

        # Recommendations
        recommendations = []
        if len(report.get("top_similar_pairs", [])) > 0:
            recommendations.append({
                "type": "dedup",
                "priority": "high" if len(report["top_similar_pairs"]) > 5 else "medium",
                "message": f"Found {len(report['top_similar_pairs'])} highly similar document pairs. Consider running dedup scan."
            })
        if archived > total * 0.3 and total > 0:
            recommendations.append({
                "type": "cleanup",
                "priority": "low",
                "message": f"{archived} of {total} documents are archived. Consider permanent deletion."
            })
        if age_distribution["older"] > total * 0.5 and total > 10:
            recommendations.append({
                "type": "stale",
                "priority": "medium",
                "message": f"{age_distribution['older']} documents are older than 90 days. Review for archival."
            })
        if report["embedding_coverage"]["without_embeddings"] > 0:
            recommendations.append({
                "type": "embeddings",
                "priority": "medium",
                "message": f"{report['embedding_coverage']['without_embeddings']} documents lack embeddings. Re-index recommended."
            })

        report["recommendations"] = recommendations

        return report

    def get_growth_timeline(
        self,
        kb_name: str,
        granularity: str = "month"
    ) -> Dict[str, Any]:
        """Get document growth timeline grouped by period."""
        docs_metadata = get_documents_metadata(kb_name)

        timeline = defaultdict(int)
        for doc in docs_metadata:
            upload_date_str = doc.get("upload_date", "")
            if upload_date_str:
                try:
                    dt = datetime.fromisoformat(upload_date_str)
                    if granularity == "day":
                        key = dt.strftime("%Y-%m-%d")
                    elif granularity == "week":
                        key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
                    else:  # month
                        key = dt.strftime("%Y-%m")
                    timeline[key] += 1
                except (ValueError, TypeError):
                    pass

        sorted_timeline = sorted(timeline.items())

        # Compute cumulative
        cumulative = []
        running_total = 0
        for period, count in sorted_timeline:
            running_total += count
            cumulative.append({"period": period, "added": count, "total": running_total})

        return {
            "kb_name": kb_name,
            "granularity": granularity,
            "timeline": cumulative,
            "total_documents": len(docs_metadata)
        }

    # ========================================================================
    # ARCHIVAL
    # ========================================================================

    def archive_documents(
        self,
        kb_name: str,
        doc_ids: List[str],
        reason: str = "manual"
    ) -> Dict[str, Any]:
        """Archive documents by setting metadata flags."""
        log_id = self.db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="archive",
            status="running",
            input_doc_ids=doc_ids,
            details={"reason": reason}
        )

        archived_count = 0
        for doc_id in doc_ids:
            success = self.db.update_document_metadata(kb_name, doc_id, {
                "archived": True,
                "archived_at": datetime.now().isoformat(),
                "archive_reason": reason
            })
            if success:
                archived_count += 1

        self.db.update_lifecycle_log(
            log_id, "completed",
            output_doc_ids=doc_ids,
            details={"archived_count": archived_count, "reason": reason}
        )

        return {
            "archived_count": archived_count,
            "doc_ids": doc_ids,
            "reason": reason
        }

    def restore_documents(
        self,
        kb_name: str,
        doc_ids: List[str]
    ) -> Dict[str, Any]:
        """Restore archived documents by clearing archive flags."""
        log_id = self.db.insert_lifecycle_log(
            kb_name=kb_name,
            operation_type="restore",
            status="running",
            input_doc_ids=doc_ids
        )

        restored_count = 0
        for doc_id in doc_ids:
            success = self.db.update_document_metadata(kb_name, doc_id, {
                "archived": False,
                "archived_at": None,
                "archive_reason": None
            })
            if success:
                restored_count += 1

        self.db.update_lifecycle_log(
            log_id, "completed",
            output_doc_ids=doc_ids,
            details={"restored_count": restored_count}
        )

        return {
            "restored_count": restored_count,
            "doc_ids": doc_ids
        }

    def find_stale_documents(
        self,
        kb_name: str,
        stale_days: int = 90
    ) -> Dict[str, Any]:
        """Find documents older than stale_days that are not archived."""
        docs_metadata = get_documents_metadata(kb_name)
        cutoff = datetime.now() - timedelta(days=stale_days)

        stale_docs = []
        for doc in docs_metadata:
            upload_date_str = doc.get("upload_date", "")
            if upload_date_str:
                try:
                    upload_date = datetime.fromisoformat(upload_date_str)
                    if upload_date < cutoff:
                        stale_docs.append({
                            "filename": doc.get("filename", "unknown"),
                            "upload_date": upload_date_str,
                            "age_days": (datetime.now() - upload_date).days
                        })
                except (ValueError, TypeError):
                    pass

        stale_docs.sort(key=lambda x: x.get("age_days", 0), reverse=True)

        return {
            "kb_name": kb_name,
            "stale_days_threshold": stale_days,
            "stale_count": len(stale_docs),
            "stale_documents": stale_docs
        }

    def list_archived(self, kb_name: str) -> Dict[str, Any]:
        """List all archived documents in a KB."""
        all_docs = self.db.get_all_embeddings(kb_name, exclude_archived=False)

        archived_docs = []
        for doc in all_docs:
            if doc["metadata"].get("archived"):
                archived_docs.append({
                    "doc_id": doc["doc_id"],
                    "content_preview": doc["content"][:200],
                    "archived_at": doc["metadata"].get("archived_at"),
                    "archive_reason": doc["metadata"].get("archive_reason", "unknown")
                })

        return {
            "kb_name": kb_name,
            "archived_count": len(archived_docs),
            "archived_documents": archived_docs
        }
