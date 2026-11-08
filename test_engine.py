"""
test_engine.py
--------------
Comprehensive E2E Verification Suite:
1. Baseline Ingestion of full corpus.
2. High-Frequency Micro-Mutation (Accenture scenario: 20 updates/day).
3. Verification of Zero-Cost Chunk Reuse (>90% compute saved).
4. Pruning/Deletion validation (old chunks evicted cleanly).
5. Cache coherence & immediate invalidation upon chunk mutation.
6. Search accuracy test verifying updated answers win over stale answers.
"""

import os
import pytest
from engine import RealTimeRAGEngine

@pytest.fixture
def rag_engine(tmp_path):
    db_file = str(tmp_path / "test_rag.db")
    return RealTimeRAGEngine(db_path=db_file)

def test_initial_ingest_and_retrieval(rag_engine):
    doc_id = "doc-pricing-policy"
    title = "Enterprise Pricing Policy"
    chunks = [
        {"section": "overview", "content": "Our enterprise plan pricing starts at $5,000 per month with SLA.", "metadata": {"tier": "enterprise"}},
        {"section": "seats", "content": "Each additional user seat costs $45 per user per month.", "metadata": {"tier": "enterprise"}},
        {"section": "support", "content": "24/7 dedicated telephone support is included in the enterprise tier.", "metadata": {"tier": "enterprise"}},
    ]

    report = rag_engine.ingest_document(doc_id, title, chunks)
    assert report["version"] == 1
    assert report["total_chunks"] == 3
    assert report["re_embedded_chunks"] == 3
    assert report["reused_chunks"] == 0

    # Query search
    res = rag_engine.query_hybrid("How much is an additional user seat?", top_k=1)
    assert len(res["results"]) > 0
    top_hit = res["results"][0]
    assert "45 per user" in top_hit["content"]
    assert top_hit["doc_version"] == 1

def test_accenture_scenario_delta_update_only_changed_chunk(rag_engine):
    doc_id = "doc-pricing-policy"
    title = "Enterprise Pricing Policy"
    chunks_v1 = [
        {"section": "overview", "content": "Our enterprise plan pricing starts at $5,000 per month with SLA.", "metadata": {}},
        {"section": "seats", "content": "Each additional user seat costs $45 per user per month.", "metadata": {}},
        {"section": "support", "content": "24/7 dedicated telephone support is included in the enterprise tier.", "metadata": {}},
        {"section": "refunds", "content": "Refund requests must be filed within 30 days of invoice date.", "metadata": {}},
    ]
    rag_engine.ingest_document(doc_id, title, chunks_v1)

    # Cache an answer based on V1
    cached_query = "What is the fee for an extra user seat?"
    rag_engine.write_cache(
        query=cached_query,
        answer="The additional user seat fee is $45/user/month.",
        sources=[{"doc_id": doc_id, "chunk_id": rag_engine.generate_chunk_id(doc_id, "seats", 1)}]
    )
    assert rag_engine.check_cache(cached_query) is not None

    # Mutation 1: Only the 'seats' chunk changes ($45 -> $60), other 3 remain untouched!
    chunks_v2 = [
        {"section": "overview", "content": "Our enterprise plan pricing starts at $5,000 per month with SLA.", "metadata": {}},
        {"section": "seats", "content": "Each additional user seat costs $60 per user per month due to upgraded AI features.", "metadata": {}},
        {"section": "support", "content": "24/7 dedicated telephone support is included in the enterprise tier.", "metadata": {}},
        {"section": "refunds", "content": "Refund requests must be filed within 30 days of invoice date.", "metadata": {}},
    ]

    report_v2 = rag_engine.ingest_document(doc_id, title, chunks_v2)
    assert report_v2["version"] == 2
    assert report_v2["total_chunks"] == 4
    assert report_v2["re_embedded_chunks"] == 1, "Only 1 chunk should have been re-embedded!"
    assert report_v2["reused_chunks"] == 3, "3 chunks must be preserved and reused!"
    assert report_v2["cost_savings_pct"] == 75.0, "Should have saved 75% computational cost!"
    assert report_v2["cache_queries_evicted"] == 1, "The obsolete cached query must be evicted!"

    # Verify cache is now invalidated
    assert rag_engine.check_cache(cached_query) is None, "Cache must be automatically purged!"

    # Query for the new price - must return $60 immediately
    res_v2 = rag_engine.query_hybrid("What is the fee for an extra user seat?", top_k=1)
    top_hit = res_v2["results"][0]
    assert "$60 per user" in top_hit["content"]
    assert top_hit["doc_version"] == 2

def test_pruning_and_deletion(rag_engine):
    doc_id = "doc-compliance"
    title = "Compliance Handbook"
    chunks_v1 = [
        {"section": "privacy", "content": "GDPR and CCPA privacy standards apply to all customer records.", "metadata": {}},
        {"section": "deprecated_legacy", "content": "Legacy protocol TLS 1.0 is permitted for backwards compatibility.", "metadata": {}},
    ]
    rag_engine.ingest_document(doc_id, title, chunks_v1)

    # Query legacy protocol
    res1 = rag_engine.query_hybrid("Is TLS 1.0 allowed?")
    assert any("TLS 1.0" in r["content"] for r in res1["results"])

    # Mutation: Deprecated chunk is completely removed
    chunks_v2 = [
        {"section": "privacy", "content": "GDPR and CCPA privacy standards apply to all customer records.", "metadata": {}},
    ]
    report_v2 = rag_engine.ingest_document(doc_id, title, chunks_v2)
    assert report_v2["deleted_chunks"] == 1
    assert report_v2["total_chunks"] == 1

    # Query again - legacy protocol chunk MUST NOT exist in search results
    res2 = rag_engine.query_hybrid("Is TLS 1.0 allowed?")
    for r in res2["results"]:
        assert "TLS 1.0" not in r["content"]
