from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from app.answer import build_answer
from app.search import search

app = FastAPI(title="Support KB Assistant", version="0.1.0")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def _source(p: dict) -> dict:
    return {
        "slug": p["slug"],
        "title": p["title"],
        "heading": p["heading"],
        "score": round(p["score"], 3),
    }


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
    return {"answer": answer, "mode": mode, "sources": [_source(p) for p in passages]}
