import uuid

import pytest
from fastapi.testclient import TestClient

from app.documents import save_document
from app.main import app
from app.tenants import create_tenant, delete_tenant, get_widget

client = TestClient(app)

DOC = (
    "# Acme printers\n\n## Quokka printer\nTo use the quokka printer press the zorblax "
    "button twice and wait for the quokka light.\n"
)


@pytest.fixture(scope="module")
def tenant():
    t, key = create_tenant("Widget test " + uuid.uuid4().hex[:6])
    save_document(t["id"], "printers.md", DOC.encode())
    yield t, key, get_widget(t["id"])["token"]
    delete_tenant(t["id"])


def test_static_pages_are_served():
    r = client.get("/panel")
    assert r.status_code == 200 and "Support KB Assistant" in r.text
    r = client.get("/widget.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "/widget/ask" in r.text
    # without a charset, other sites would show the accents and the emoji broken
    assert "utf-8" in r.headers["content-type"].lower()


def test_widget_answers_from_the_clients_documents(tenant):
    _, _, token = tenant
    r = client.post("/widget/ask", json={"token": token, "question": "how do I use the quokka printer zorblax?"})
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] and body["sources"][0]["slug"] == "printers"
    assert "secret" not in str(body).lower()


def test_widget_token_is_not_the_secret_key(tenant):
    _, key, token = tenant
    assert key != token
    # the public token cannot manage documents or read the report
    assert client.get("/tenant/documents", headers={"X-API-Key": token}).status_code == 401
    assert client.get("/tenant/report", headers={"X-API-Key": token}).status_code == 401
    # and the secret key is not accepted as a widget token
    r = client.post("/widget/ask", json={"token": key, "question": "quokka printer"})
    assert r.status_code == 401


def test_invalid_widget_token():
    r = client.post("/widget/ask", json={"token": "wk_nope", "question": "anything here"})
    assert r.status_code == 401


def test_allowed_origins_are_enforced(tenant):
    _, key, token = tenant
    r = client.put("/tenant/widget", json={"allowed_origins": ["https://www.cliente.com/", "not a site"]},
                   headers={"X-API-Key": key})
    assert r.status_code == 422
    r = client.put("/tenant/widget", json={"allowed_origins": ["https://www.Cliente.com/"]},
                   headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["allowed_origins"] == ["https://www.cliente.com"]
    q = {"token": token, "question": "how do I use the quokka printer?"}
    assert client.post("/widget/ask", json=q, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/widget/ask", json=q).status_code == 403
    assert client.post("/widget/ask", json=q, headers={"Origin": "https://www.cliente.com"}).status_code == 200
    client.put("/tenant/widget", json={"allowed_origins": []}, headers={"X-API-Key": key})
    assert client.post("/widget/ask", json=q, headers={"Origin": "https://anything.example"}).status_code == 200


def test_snippet_uses_public_url(tenant, monkeypatch):
    _, key, token = tenant
    monkeypatch.setenv("PUBLIC_URL", "https://kb.example.com/")
    r = client.get("/tenant/widget", headers={"X-API-Key": key}).json()
    assert r["snippet"] == f'<script src="https://kb.example.com/widget.js" data-token="{token}" async></script>'


def test_cors_preflight_allows_the_widget():
    r = client.options("/widget/ask", headers={
        "Origin": "https://www.cliente.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "https://www.cliente.com")


def test_widget_visitor_limit(tenant, monkeypatch):
    _, _, token = tenant
    monkeypatch.setenv("WIDGET_VISITOR_PER_MINUTE", "2")
    q = {"token": token, "question": "how do I use the quokka printer?"}
    codes = [client.post("/widget/ask", json=q, headers={"X-Forwarded-For": "7.7.7.7"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.post("/widget/ask", json=q, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200


def test_widget_questions_appear_in_the_clients_report(tenant):
    _, key, token = tenant
    client.post("/widget/ask", json={"token": token, "question": "something never documented anywhere"})
    rep = client.get("/tenant/report", headers={"X-API-Key": key}).json()
    assert "something never documented anywhere" in [u["question"] for u in rep["unanswered_questions"]]


def test_home_page_links_to_the_client_panel():
    html = client.get("/").text
    assert 'href="/panel"' in html and 'clients: "Client login"' in html and "Acceso clientes" in html


# ---- documents to review ----------------------------------------------------

def _age(tenant_id, slug, days):
    """Pretend a document was uploaded `days` days ago."""
    from app.db import connect
    with connect() as conn:
        conn.execute(
            "UPDATE chunks SET created_at = now() - make_interval(days => %s) WHERE tenant_id = %s AND slug = %s",
            (days, tenant_id, slug),
        )


def _review(key, **params):
    r = client.get("/tenant/report", headers={"X-API-Key": key}, params=params)
    return {d["slug"]: d for d in r.json()["documents_to_review"]}


def test_old_document_is_marked_stale_and_reupload_resets_it(tenant):
    t, key, _ = tenant
    assert "printers" not in _review(key)             # just uploaded: fine
    _age(t["id"], "printers", 200)
    assert "stale" in _review(key)["printers"]["reasons"]
    save_document(t["id"], "printers.md", DOC.encode())  # updating the document
    assert "printers" not in _review(key)


def test_document_with_not_helpful_votes_is_flagged(tenant):
    t, key, token = tenant
    q = {"token": token, "question": "how do I use the quokka printer zorblax?"}
    ids = [client.post("/widget/ask", json=q, headers={"X-Forwarded-For": f"5.5.5.{i}"}).json()["query_id"] for i in range(3)]
    for qid in ids[:2]:
        client.post("/feedback", json={"query_id": qid, "helpful": False}, headers={"X-API-Key": key})
    client.post("/feedback", json={"query_id": ids[2], "helpful": True}, headers={"X-API-Key": key})
    item = _review(key)["printers"]
    assert "rated_not_helpful" in item["reasons"]
    assert item["rated_not_helpful"] == 2 and item["rated_helpful"] == 1 and item["times_used"] >= 3


def test_unused_document_needs_age_and_is_not_flagged_when_new(tenant):
    t, key, _ = tenant
    save_document(t["id"], "never-asked.md", b"# Old policy\n\n## Wombat rules\nThe wombat rules apply to every wombat meeting room.")
    assert "never-asked" not in _review(key)           # new: not "unused" yet
    _age(t["id"], "never-asked", 45)
    assert "unused" in _review(key, days=30)["never-asked"]["reasons"]


def test_review_list_is_scoped_to_the_client(tenant):
    t, key, _ = tenant
    other, other_key = create_tenant("Other " + uuid.uuid4().hex[:6])
    try:
        save_document(other["id"], "other-doc.md", b"# Other\n\n## Part\nSome text about giraffes and tigers.")
        _age(other["id"], "other-doc", 400)
        assert "other-doc" in _review(other_key) and "other-doc" not in _review(key)
    finally:
        delete_tenant(other["id"])


def test_report_has_weekly_totals():
    from app.analytics import log_query, report
    from app.tenants import create_tenant, delete_tenant
    row, _ = create_tenant("weekly-test")
    try:
        log_query("alpha question", "answer", [{"slug": "x", "score": 1.0}], tenant_id=row["id"])
        log_query("beta question", "no_match", [], tenant_id=row["id"])
        r = report(30, row["id"])
        assert r["answered"] == 1 and r["questions"] == 2
        assert sum(w["questions"] for w in r["by_week"]) == 2
        assert sum(w["answered"] for w in r["by_week"]) == 1
    finally:
        delete_tenant(row["id"])


def test_privacy_page_is_served_with_contact_email(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("CONTACT_EMAIL", "hola@example.com")
    c = TestClient(app)
    r = c.get("/privacy")
    assert r.status_code == 200 and "hola@example.com" in r.text and "Borrador" in r.text
    assert 'href="/privacy"' in c.get("/").text


DOC = (
    "# Zorblax setup\n\n## Quillmoth install\n\nRun the quillmoth installer and restart. " * 3
    + "\n\n## Frobnitz reset\n\nHold the frobnitz button for ten seconds to reset it. " * 3
)


@pytest.fixture
def fresh():
    from app.tenants import create_tenant, delete_tenant
    row, key = create_tenant("quality-test")
    yield key
    delete_tenant(row["id"])


def test_quality_check_scores_the_clients_own_documents(fresh):
    key = fresh
    assert client.post("/tenant/documents", files={"files": ("zorblax.md", DOC.encode(), "text/markdown")},
                headers={"X-API-Key": key}).status_code == 200
    r = client.post("/tenant/quality", headers={"X-API-Key": key})
    assert r.status_code == 200
    j = r.json()
    assert j["tested"] >= 2 and j["method"] in ("headings", "claude")
    assert j["top3"] == 1.0 and j["missed"] == []


def test_quality_check_lists_what_was_not_found(fresh, monkeypatch):
    import app.quality as quality
    key = fresh
    assert client.post("/tenant/documents", files={"files": ("zorblax.md", DOC.encode(), "text/markdown")},
                headers={"X-API-Key": key}).status_code == 200
    monkeypatch.setattr(quality, "make_question", lambda c: ("nothing related at all", "claude"))
    j = client.post("/tenant/quality", headers={"X-API-Key": key}).json()
    assert j["top3"] == 0.0 and j["missed"] and j["method"] == "claude"


def test_quality_check_needs_a_key_and_a_document():
    from app.tenants import create_tenant, delete_tenant
    assert client.post("/tenant/quality").status_code == 401
    row, key = create_tenant("empty-quality")
    try:
        j = client.post("/tenant/quality", headers={"X-API-Key": key}).json()
        assert j["tested"] == 0 and j["top3"] is None
    finally:
        delete_tenant(row["id"])
