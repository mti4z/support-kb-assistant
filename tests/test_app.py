import pytest
from fastapi.testclient import TestClient

from app.ingest import ingest, parse_article, ARTICLES_DIR
from app.main import app
from app.search import search


@pytest.fixture(scope="session", autouse=True)
def loaded_db():
    ingest()


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
