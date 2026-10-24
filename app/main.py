import json
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.analytics import log_query, report, save_feedback
from app.answer import build_answer
from app.documents import (
    MAX_BYTES, DocumentError, delete_document, list_documents, save_document,
)
from app.ratelimit import check_questions, check_uploads
from app.search import search
from app.ratelimit import check_widget
from app.tenants import (
    get_tenant, get_tenant_by_widget_token, get_widget, set_allowed_origins,
)

app = FastAPI(title="Support KB Assistant", version="0.4.0")
# The widget runs on the client's website, so browsers need CORS. No cookies are
# used: access is always by an explicit key in the header or body.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])


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


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


@app.get("/", include_in_schema=False)
def home():
    """The page. CONTACT_EMAIL (environment variable) fills the contact button,
    so the address does not have to be written in the source code."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    email = os.environ.get("CONTACT_EMAIL", "").strip()
    if EMAIL_RE.match(email):
        html = re.sub(
            r'var CONTACT_EMAIL = "[^"]*";',
            lambda _: f"var CONTACT_EMAIL = {json.dumps(email)};",
            html,
            count=1,
        )
    return HTMLResponse(html)


@app.get("/health")
def health():
    return {"status": "ok"}


def optional_tenant_id(key: str | None) -> int | None:
    """No key: the public demo. A key that does not exist is an error, never a silent fallback."""
    if not key:
        return None
    tenant = get_tenant(key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid X-API-Key")
    return tenant["id"]


def required_tenant(key: str | None) -> dict:
    if not key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")
    tenant = get_tenant(key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid X-API-Key")
    return tenant


@app.get("/search")
def search_endpoint(
    request: Request,
    q: str = Query(min_length=2),
    limit: int = Query(5, ge=1, le=20),
    x_api_key: str | None = Header(default=None),
):
    tenant_id = optional_tenant_id(x_api_key)
    check_questions(request, tenant_id)
    return {
        "results": [
            {**_source(p), "content": p["content"]} for p in search(q, limit, tenant_id=tenant_id)
        ]
    }


@app.post("/ask")
def ask(req: AskRequest, request: Request, x_api_key: str | None = Header(default=None)):
    """Without X-API-Key it uses the public demo articles; with the key of a
    client it uses only that client's documents."""
    tenant_id = optional_tenant_id(x_api_key)
    check_questions(request, tenant_id)
    passages = search(req.question, limit=3, tenant_id=tenant_id)
    answer, mode = build_answer(req.question, passages)
    return {
        "answer": answer,
        "mode": mode,
        "sources": [_source(p) for p in passages],
        "query_id": log_query(req.question, mode, passages, tenant_id),
    }


@app.post("/feedback")
def feedback(req: FeedbackRequest, x_api_key: str | None = Header(default=None)):
    if not save_feedback(req.query_id, req.helpful, optional_tenant_id(x_api_key)):
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


# ---- Client area: each client manages only their own documents -------------

@app.post("/tenant/documents")
async def upload_documents(files: list[UploadFile], x_api_key: str | None = Header(default=None)):
    """Upload one or more .md, .txt, .pdf or .docx files. Same name = replace."""
    tenant = required_tenant(x_api_key)
    check_uploads(tenant["id"])
    results = []
    for f in files:
        data = await f.read(MAX_BYTES + 1)
        name = f.filename or "document"
        try:
            results.append({"file": name, "ok": True, **save_document(tenant["id"], name, data)})
        except DocumentError as e:
            results.append({"file": name, "ok": False, "error": str(e)})
    return {"results": results}


@app.get("/tenant/documents")
def get_documents(x_api_key: str | None = Header(default=None)):
    tenant = required_tenant(x_api_key)
    return {"documents": list_documents(tenant["id"])}


@app.delete("/tenant/documents/{slug}")
def remove_document(slug: str, x_api_key: str | None = Header(default=None)):
    tenant = required_tenant(x_api_key)
    if not delete_document(tenant["id"], slug):
        raise HTTPException(status_code=404, detail="Unknown document")
    return {"ok": True}


@app.get("/tenant/report")
def tenant_report(days: int = Query(30, ge=1, le=365), x_api_key: str | None = Header(default=None)):
    """Unanswered questions and usage of this client only."""
    tenant = required_tenant(x_api_key)
    return report(days, tenant["id"])


# ---- Panel and widget -----------------------------------------------------

STATIC = Path(__file__).parent / "static"
ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


@app.get("/privacy", include_in_schema=False)
def privacy():
    """Privacy and terms (draft). CONTACT_EMAIL fills the contact link, like the home page."""
    html = (STATIC / "privacy.html").read_text(encoding="utf-8")
    email = os.environ.get("CONTACT_EMAIL", "").strip()
    if EMAIL_RE.match(email):
        html = re.sub(
            r'var CONTACT_EMAIL = "[^"]*";',
            lambda _: f"var CONTACT_EMAIL = {json.dumps(email)};",
            html,
            count=1,
        )
    return HTMLResponse(html)


@app.get("/panel", include_in_schema=False)
def panel():
    """The client panel. The page itself holds no data: it asks the API with the client's key."""
    return FileResponse(STATIC / "panel.html", media_type="text/html")


@app.get("/widget.js", include_in_schema=False)
def widget_js():
    return FileResponse(
        STATIC / "widget.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


def public_url(request: Request) -> str:
    configured = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


class WidgetAsk(BaseModel):
    token: str = Field(min_length=3, max_length=100)
    question: str = Field(min_length=3, max_length=500)


class OriginsUpdate(BaseModel):
    allowed_origins: list[str] = Field(max_length=20)


@app.post("/widget/ask")
def widget_ask(req: WidgetAsk, request: Request):
    """Question from the widget on a client's site. The widget key can only ask."""
    tenant = get_tenant_by_widget_token(req.token)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid widget token")
    origin = request.headers.get("origin")
    allowed = list(tenant["allowed_origins"])
    if allowed and origin not in allowed:
        raise HTTPException(status_code=403, detail="This site is not allowed to use the widget")
    check_widget(request, tenant["id"])
    passages = search(req.question, limit=3, tenant_id=tenant["id"])
    answer, mode = build_answer(req.question, passages)
    return {
        "answer": answer,
        "mode": mode,
        "sources": [{"slug": p["slug"], "title": p["title"], "heading": p["heading"]} for p in passages],
        "query_id": log_query(req.question, mode, passages, tenant["id"]),
    }


@app.get("/tenant/widget")
def tenant_widget(request: Request, x_api_key: str | None = Header(default=None)):
    tenant = required_tenant(x_api_key)
    w = get_widget(tenant["id"])
    snippet = f'<script src="{public_url(request)}/widget.js" data-token="{w["token"]}" async></script>'
    return {"token": w["token"], "allowed_origins": w["allowed_origins"], "snippet": snippet}


@app.put("/tenant/widget")
def update_widget(body: OriginsUpdate, x_api_key: str | None = Header(default=None)):
    tenant = required_tenant(x_api_key)
    origins = [o.strip().rstrip("/").lower() for o in body.allowed_origins if o.strip()]
    for o in origins:
        if not ORIGIN_RE.match(o):
            raise HTTPException(status_code=422, detail=f"Not a valid site address: {o} (use https://www.example.com)")
    set_allowed_origins(tenant["id"], list(dict.fromkeys(origins)))
    return {"allowed_origins": get_widget(tenant["id"])["allowed_origins"]}
