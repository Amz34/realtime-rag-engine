"""
app.py
------
FastAPI Production Microservice exposing:
- POST /api/v1/documents/delta-ingest (Stream/Webhook mutation receiver)
- POST /api/v1/search (Real-time hybrid RAG with cache invalidation check)
- POST /api/v1/cache/register (Write generated answer to chunk-tagged cache)
- GET /api/v1/health & GET /api/v1/stats (System monitoring & telemetry)
"""

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import os
import time
from engine import RealTimeRAGEngine

app = FastAPI(
    title="Real-Time Streaming RAG Vector Engine",
    description="Incremental Delta Chunking, Dynamic HNSW UPSERT & Tagged Semantic Cache",
    version="2.0.0"
)

# Shared in-memory engine or file-backed database
DB_PATH = os.getenv("RAG_DB_PATH", "/tmp/realtime_rag.db")
rag_engine = RealTimeRAGEngine(db_path=DB_PATH)

class ChunkPayload(BaseModel):
    section: str = Field(..., description="Breadcrumb or section title")
    content: str = Field(..., description="Text content of the chunk")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class DeltaIngestRequest(BaseModel):
    doc_id: str = Field(..., description="Stable unique identifier for the document")
    title: str = Field(..., description="Document title")
    chunks: List[ChunkPayload] = Field(..., description="All current chunks of the document")
    version: Optional[int] = Field(default=None, description="Explicit version override")

class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    top_k: int = Field(default=5, ge=1, le=50)

class CacheRegisterRequest(BaseModel):
    query: str
    answer: str
    sources: List[Dict[str, Any]]

@app.get("/api/v1/health")
def health():
    return {"status": "healthy", "service": "realtime-rag-engine", "timestamp": time.time()}

@app.post("/api/v1/documents/delta-ingest")
def delta_ingest(req: DeltaIngestRequest):
    try:
        chunks_data = [c.model_dump() for c in req.chunks]
        result = rag_engine.ingest_document(
            doc_id=req.doc_id,
            title=req.title,
            chunks_data=chunks_data,
            doc_version=req.version
        )
        return {"status": "success", "delta_report": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/search")
def search(req: SearchRequest):
    try:
        res = rag_engine.query_hybrid(query=req.query, top_k=req.top_k)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/cache/register")
def register_cache(req: CacheRegisterRequest):
    try:
        rag_engine.write_cache(query=req.query, answer=req.answer, sources=req.sources)
        return {"status": "cached", "query": req.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
