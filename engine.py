"""
engine.py
---------
Production-grade Real-Time Incremental RAG Vector Engine:
- Stable Deterministic UUIDv5 + Content Hashing
- Delta Diffing (zero re-embedding for unchanged chunks)
- Atomic In-Place Index Mutation (Insert / Update / Delete)
- Real-Time Hybrid Search (Cosine Vector Similarity + Inverted BM25)
- Document Version Filtering & Temporal Freshness Re-Ranking
- Tag-based Semantic Cache with Automated Chunk-Level Invalidation
"""

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from rank_bm25 import BM25Okapi

CHUNK_UUID_NAMESPACE = uuid.UUID("3c4d5e6f-7a8b-9c0d-1e2f-3a4b5c6d7e8f")
_WORD_REGEX = re.compile(r"\w+")


_STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both",
    "but", "by", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "had", "hadn't",
    "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's",
    "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on",
    "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own",
    "same", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some",
    "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then",
    "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we",
    "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's",
    "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "with",
    "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours"
}

class FastLocalEmbedder:
    """
    Normalized dense semantic embedder with content word emphasis, bigram hashing,
    and stop-word attenuation.
    """
    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def _embed_single(self, text: str) -> List[float]:
        tokens = [t for t in _WORD_REGEX.findall(text.lower())]
        vec = np.zeros(self.dimension, dtype=np.float32)
        if not tokens:
            return vec.tolist()

        for i, token in enumerate(tokens):
            weight = 0.05 if token in _STOP_WORDS else 2.5
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dimension
            sign = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += sign * weight

            # Bigram feature
            if i > 0:
                bigram = f"{tokens[i-1]}_{token}"
                hb = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16)
                idx_b = hb % self.dimension
                sign_b = 1.0 if ((hb >> 8) & 1) else -1.0
                vec[idx_b] += sign_b * (weight * 0.8)

        norm = np.linalg.norm(vec)
        if norm > 1e-9:
            vec /= norm
        return vec.tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_single(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed_single(text)


class RealTimeRAGEngine:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.embedder = FastLocalEmbedder(dimension=384)
        self._lock = threading.RLock()
        self._conn = None
        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._init_storage()

        # Semantic Cache: query_hash -> { 'answer': str, 'sources': list, 'chunk_ids': list, 'timestamp': float }
        self._semantic_cache: Dict[str, Dict[str, Any]] = {}
        # Inverted Cache Index: chunk_id -> set of query_hashes
        self._chunk_to_cache_keys: Dict[str, Set[str]] = defaultdict(set)
        # Document to cache keys
        self._doc_to_cache_keys: Dict[str, Set[str]] = defaultdict(set)

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_storage(self):
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    current_version INTEGER NOT NULL DEFAULT 1,
                    total_chunks INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    doc_version INTEGER NOT NULL,
                    chunk_hash TEXT NOT NULL,
                    section_path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_lookup ON document_chunks (doc_id, chunk_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_chunks_version ON document_chunks (doc_id, doc_version)")
            conn.commit()

    @staticmethod
    def compute_content_hash(text: str) -> str:
        normalized = " ".join(text.strip().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_chunk_id(doc_id: str, section_path: str, chunk_index: int) -> str:
        raw_key = f"{doc_id}:{section_path}:{chunk_index}"
        return str(uuid.uuid5(CHUNK_UUID_NAMESPACE, raw_key))

    def ingest_document(
        self,
        doc_id: str,
        title: str,
        chunks_data: List[Dict[str, Any]], # [{'section': str, 'content': str, 'metadata': dict}]
        doc_version: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Incremental Delta Ingestion:
        1. Hashes incoming chunks.
        2. Detects unchanged vs. mutated vs. deleted chunks.
        3. Embeds ONLY modified chunks.
        4. Atomic UPSERT and deletion.
        5. Automatically purges invalidated semantic cache entries.
        """
        start_time = time.perf_counter()
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()

            # Retrieve current doc state
            cur.execute("SELECT current_version FROM documents WHERE doc_id = ?", (doc_id,))
            doc_row = cur.fetchone()
            if doc_row:
                assigned_version = doc_version if doc_version is not None else (doc_row["current_version"] + 1)
            else:
                assigned_version = doc_version if doc_version is not None else 1
                cur.execute(
                    "INSERT INTO documents (doc_id, title, current_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, title, assigned_version, time.time(), time.time())
                )

            # Retrieve existing chunks for this document
            cur.execute("SELECT chunk_id, chunk_hash FROM document_chunks WHERE doc_id = ?", (doc_id,))
            existing_chunks = {row["chunk_id"]: row["chunk_hash"] for row in cur.fetchall()}

            incoming_chunk_records = []
            seen_chunk_ids = set()
            to_embed_records = []
            unchanged_chunk_ids = []

            for idx, item in enumerate(chunks_data):
                section = item.get("section", "root")
                content = item.get("content", "")
                meta = item.get("metadata", {})
                ch_hash = self.compute_content_hash(content)
                ch_id = self.generate_chunk_id(doc_id, section, idx)
                seen_chunk_ids.add(ch_id)

                record = {
                    "chunk_id": ch_id,
                    "doc_id": doc_id,
                    "doc_version": assigned_version,
                    "chunk_hash": ch_hash,
                    "section_path": section,
                    "chunk_index": idx,
                    "content": content,
                    "metadata": meta,
                }
                incoming_chunk_records.append(record)

                if ch_id in existing_chunks and existing_chunks[ch_id] == ch_hash:
                    unchanged_chunk_ids.append(ch_id)
                else:
                    to_embed_records.append(record)

            # Deleted chunks = chunks in DB that aren't in this new incoming set
            deleted_chunk_ids = set(existing_chunks.keys()) - seen_chunk_ids

            # Compute embeddings ONLY for mutated chunks
            if to_embed_records:
                embeddings = self.embedder.embed_documents([r["content"] for r in to_embed_records])
                for r, emb in zip(to_embed_records, embeddings):
                    r["embedding_json"] = json.dumps(emb)

            now = time.time()

            # Execute UPSERT for modified chunks
            for r in to_embed_records:
                cur.execute("""
                    INSERT INTO document_chunks (
                        chunk_id, doc_id, doc_version, chunk_hash, section_path,
                        chunk_index, content, metadata_json, embedding_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        doc_version = excluded.doc_version,
                        chunk_hash = excluded.chunk_hash,
                        section_path = excluded.section_path,
                        content = excluded.content,
                        metadata_json = excluded.metadata_json,
                        embedding_json = excluded.embedding_json,
                        updated_at = excluded.updated_at
                """, (
                    r["chunk_id"], r["doc_id"], r["doc_version"], r["chunk_hash"],
                    r["section_path"], r["chunk_index"], r["content"],
                    json.dumps(r["metadata"]), r["embedding_json"], now, now
                ))

            # Update doc_version timestamp on unchanged chunks so they match the active release
            for ch_id in unchanged_chunk_ids:
                cur.execute(
                    "UPDATE document_chunks SET doc_version = ?, updated_at = ? WHERE chunk_id = ?",
                    (assigned_version, now, ch_id)
                )

            # Delete pruned chunks
            if deleted_chunk_ids:
                cur.executemany(
                    "DELETE FROM document_chunks WHERE chunk_id = ?",
                    [(cid,) for cid in deleted_chunk_ids]
                )

            # Update document master row
            cur.execute(
                "UPDATE documents SET current_version = ?, total_chunks = ?, updated_at = ? WHERE doc_id = ?",
                (assigned_version, len(incoming_chunk_records), now, doc_id)
            )
            conn.commit()

            # Evict cache for changed & deleted chunks
            impacted_chunk_ids = [r["chunk_id"] for r in to_embed_records] + list(deleted_chunk_ids)
            evicted_queries = self._invalidate_cache(doc_id, impacted_chunk_ids)

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            return {
                "doc_id": doc_id,
                "version": assigned_version,
                "total_chunks": len(incoming_chunk_records),
                "reused_chunks": len(unchanged_chunk_ids),
                "re_embedded_chunks": len(to_embed_records),
                "deleted_chunks": len(deleted_chunk_ids),
                "cache_queries_evicted": evicted_queries,
                "duration_ms": round(elapsed_ms, 2),
                "cost_savings_pct": round((len(unchanged_chunk_ids) / max(len(incoming_chunk_records), 1)) * 100.0, 2)
            }

    def _invalidate_cache(self, doc_id: str, impacted_chunk_ids: List[str]) -> int:
        """Surgically purges semantic cache queries dependent on updated chunks."""
        evicted_keys = set()
        for cid in impacted_chunk_ids:
            if cid in self._chunk_to_cache_keys:
                for qk in self._chunk_to_cache_keys[cid]:
                    if qk in self._semantic_cache:
                        del self._semantic_cache[qk]
                        evicted_keys.add(qk)
                del self._chunk_to_cache_keys[cid]

        if doc_id in self._doc_to_cache_keys:
            for qk in self._doc_to_cache_keys[doc_id]:
                if qk in self._semantic_cache:
                    del self._semantic_cache[qk]
                    evicted_keys.add(qk)
            del self._doc_to_cache_keys[doc_id]

        return len(evicted_keys)

    def write_cache(self, query: str, answer: str, sources: List[Dict[str, Any]]):
        """Register query answer with granular provenance chunk tags."""
        q_hash = self.compute_content_hash(query)
        chunk_ids = [s.get("chunk_id") for s in sources if s.get("chunk_id")]
        doc_ids = list({s.get("doc_id") for s in sources if s.get("doc_id")})

        self._semantic_cache[q_hash] = {
            "answer": answer,
            "sources": sources,
            "chunk_ids": chunk_ids,
            "timestamp": time.time()
        }

        for cid in chunk_ids:
            self._chunk_to_cache_keys[cid].add(q_hash)
        for did in doc_ids:
            self._doc_to_cache_keys[did].add(q_hash)

    def check_cache(self, query: str) -> Optional[Dict[str, Any]]:
        q_hash = self.compute_content_hash(query)
        entry = self._semantic_cache.get(q_hash)
        if entry:
            return {
                "hit": True,
                "answer": entry["answer"],
                "sources": entry["sources"],
                "timestamp": entry["timestamp"]
            }
        return None

    def query_hybrid(
        self,
        query: str,
        top_k: int = 5,
        reciprocal_k: int = 60,
        temporal_freshness_weight: float = 0.15
    ) -> Dict[str, Any]:
        """
        Ultra-High Accuracy Hybrid Search:
        1. Checks Chunk-Tagged Semantic Cache.
        2. Vector Cosine Similarity Search over active latest doc_version.
        3. Lexical BM25 Scoring with word overlap.
        4. Reciprocal Rank Fusion (RRF) + Temporal Freshness Decay Boosting.
        """
        start_time = time.perf_counter()

        # Check Cache
        cached = self.check_cache(query)
        if cached:
            cached["latency_ms"] = round((time.perf_counter() - start_time) * 1000.0, 3)
            return cached

        q_vec = np.array(self.embedder.embed_query(query), dtype=np.float32)
        q_tokens = set(_WORD_REGEX.findall(query.lower()))

        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            # Fetch all active chunks for current document versions
            cur.execute("""
                SELECT c.chunk_id, c.doc_id, c.doc_version, c.section_path,
                       c.chunk_index, c.content, c.metadata_json, c.embedding_json, c.updated_at
                FROM document_chunks c
                JOIN documents d ON c.doc_id = d.doc_id AND c.doc_version = d.current_version
            """)
            rows = cur.fetchall()

        if not rows:
            return {
                "hit": False,
                "results": [],
                "latency_ms": round((time.perf_counter() - start_time) * 1000.0, 2)
            }

        vector_scores = []
        candidates = []
        corpus_tokens = []

        now = time.time()
        for idx, row in enumerate(rows):
            emb = np.array(json.loads(row["embedding_json"]), dtype=np.float32)
            cos_sim = float(np.dot(q_vec, emb))
            vector_scores.append((idx, cos_sim))

            text = row["content"].lower()
            tokens = _WORD_REGEX.findall(text)
            corpus_tokens.append(tokens if tokens else [""])
            candidates.append(row)

        # High-accuracy BM25Okapi scoring
        q_tok_list = _WORD_REGEX.findall(query.lower())
        bm25 = BM25Okapi(corpus_tokens)
        bm25_scores = bm25.get_scores(q_tok_list)
        lexical_scores = [(idx, float(bm25_scores[idx])) for idx in range(len(rows))]

        # Rank both sets
        vector_ranked = sorted(vector_scores, key=lambda x: x[1], reverse=True)
        lexical_ranked = sorted(lexical_scores, key=lambda x: x[1], reverse=True)

        # Reciprocal Rank Fusion with Temporal Freshness
        rrf_map: Dict[int, float] = defaultdict(float)
        for rank, (c_idx, _) in enumerate(vector_ranked):
            rrf_map[c_idx] += 1.0 / (reciprocal_k + rank + 1)
        for rank, (c_idx, _) in enumerate(lexical_ranked):
            rrf_map[c_idx] += 1.0 / (reciprocal_k + rank + 1)

        # Apply recency boost: newer updates within last 24h gain up to +15% score
        for c_idx in rrf_map:
            updated_at = candidates[c_idx]["updated_at"]
            age_hours = max(0.0, (now - updated_at) / 3600.0)
            freshness_multiplier = 1.0 + (temporal_freshness_weight / (1.0 + age_hours * 0.1))
            rrf_map[c_idx] *= freshness_multiplier

        top_indices = sorted(rrf_map.keys(), key=lambda i: rrf_map[i], reverse=True)[:top_k]

        results = []
        for rank, idx in enumerate(top_indices, 1):
            row = candidates[idx]
            meta = json.loads(row["metadata_json"])
            results.append({
                "rank": rank,
                "score": round(rrf_map[idx], 4),
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "doc_version": row["doc_version"],
                "section": row["section_path"],
                "content": row["content"],
                "metadata": meta,
                "updated_at": row["updated_at"]
            })

        latency = (time.perf_counter() - start_time) * 1000.0
        return {
            "hit": False,
            "results": results,
            "latency_ms": round(latency, 2)
        }
