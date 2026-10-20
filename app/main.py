import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.analytics import log_query, report, save_feedback
from app.answer import build_answer
from app.search import search

app = FastAPI(title="Support KB Assistant", version="0.2.0")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class FeedbackRequest(BaseModel):
    query_id: int = Field(ge=1)
    helpful: bool


def _source(p: dict) -> dict:
    return {
        "slug": p["slug"],
        "title": p["title"],
        "heading": p["heading"],
        "score": round(p["score"], 3),
    }


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(INDEX_HTML)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/search")
def search_endpoint(q: str = Query(min_length=2), limit: int = Query(5, ge=1, le=20)):
    return {"results": [{**_source(p), "content": p["content"]} for p in search(q, limit)]}


@app.post("/ask")
def ask(req: AskRequest):
    passages = search(req.question, limit=3)
    answer, mode = build_answer(req.question, passages)
    return {
        "answer": answer,
        "mode": mode,
        "sources": [_source(p) for p in passages],
        "query_id": log_query(req.question, mode, passages),
    }


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    if not save_feedback(req.query_id, req.helpful):
        raise HTTPException(status_code=404, detail="Unknown query_id")
    return {"ok": True}


@app.get("/admin/report")
def admin_report(
    days: int = Query(30, ge=1, le=365),
    x_admin_token: str | None = Header(default=None),
):
    """Questions with no answer, answer rate, feedback. Needs the ADMIN_TOKEN secret."""
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:  # not configured: pretend the endpoint does not exist
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
    return report(days)
