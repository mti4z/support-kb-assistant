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
