import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.ingest import ingest, parse_article, ARTICLES_DIR
from app.main import app
from app.search import search


@pytest.fixture(scope="session", autouse=True)
def loaded_db():
    ingest()
    # The tests ask questions, which get logged. Remove those rows when finished
    # so running the tests never pollutes the data of your local database.
    with connect() as conn:
        started = conn.execute("SELECT now() AS t").fetchone()["t"]
    yield
    with connect() as conn:
        conn.execute("DELETE FROM query_log WHERE asked_at >= %s", (started,))


client = TestClient(app)


def test_parse_article_splits_sections():
    chunks = parse_article(ARTICLES_DIR / "password-reset.md")
    assert len(chunks) == 3
    assert chunks[0]["title"] == "Resetting your company password"
    assert chunks[0]["heading"] == "Self-service reset"


def test_search_finds_relevant_article():
    results = search("I forgot my password")
    assert results and results[0]["slug"] == "password-reset"


def test_search_empty_question_returns_nothing():
    assert search("   ") == []


def test_search_stopwords_only_does_not_crash():
    assert search("the and of") == []


def test_ask_returns_answer_with_sources():
    r = client.post("/ask", json={"question": "The printer says it is offline"})
    assert r.status_code == 200
    body = r.json()
    assert body["sources"][0]["slug"] == "printer-not-working"
    assert body["mode"] in {"extractive", "claude"}


def test_ask_out_of_scope_abstains():
    r = client.post("/ask", json={"question": "What is the capital of France?"})
    body = r.json()
    assert body["mode"] == "no_match" and body["sources"] == []


def test_ask_validates_input():
    assert client.post("/ask", json={"question": "a"}).status_code == 422


def test_root_serves_the_web_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Support KB Assistant" in r.text


def test_ask_logs_the_question_and_returns_its_id():
    body = client.post("/ask", json={"question": "The printer says it is offline"}).json()
    assert isinstance(body["query_id"], int)
    with connect() as conn:
        row = conn.execute("SELECT * FROM query_log WHERE id = %s", (body["query_id"],)).fetchone()
    assert row["answered"] is True and row["top_slug"] == "printer-not-working"


def test_unanswered_question_is_logged_as_not_answered():
    body = client.post("/ask", json={"question": "What is the capital of France?"}).json()
    with connect() as conn:
        row = conn.execute("SELECT * FROM query_log WHERE id = %s", (body["query_id"],)).fetchone()
    assert row["answered"] is False and row["top_slug"] is None


def test_feedback_is_saved():
    qid = client.post("/ask", json={"question": "The printer says it is offline"}).json()["query_id"]
    assert client.post("/feedback", json={"query_id": qid, "helpful": False}).status_code == 200
    with connect() as conn:
        row = conn.execute("SELECT helpful FROM query_log WHERE id = %s", (qid,)).fetchone()
    assert row["helpful"] is False


def test_feedback_for_unknown_question_is_404():
    assert client.post("/feedback", json={"query_id": 999999999, "helpful": True}).status_code == 404


def test_report_is_hidden_when_no_admin_token_is_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get("/admin/report", headers={"X-Admin-Token": "anything"}).status_code == 404


def test_report_rejects_a_wrong_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-for-tests")
    assert client.get("/admin/report").status_code == 401
    assert client.get("/admin/report", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_report_lists_frequent_unanswered_questions(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-for-tests")
    for _ in range(3):
        client.post("/ask", json={"question": "Where is the Cafeteria?"})
    r = client.get("/admin/report", headers={"X-Admin-Token": "secret-for-tests"})
    assert r.status_code == 200
    data = r.json()
    top = data["unanswered_questions"][0]
    assert top["question"] == "where is the cafeteria?" and top["times"] >= 3
    assert data["answer_rate"] is not None
