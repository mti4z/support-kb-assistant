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


# ---- synonyms ----

from app.ingest import SYNONYMS_FILE, normalize_term, parse_synonyms
from app.search import expand_query


def test_normalize_term_handles_case_and_hyphens():
    assert normalize_term("Wi-Fi") == "wi fi"
    assert normalize_term("  E-Mail ") == "e mail"


def test_parse_synonyms_ignores_comments_and_groups_terms():
    rows = parse_synonyms(SYNONYMS_FILE)
    assert rows and all(r["term"] == r["term"].strip() for r in rows)
    computer_group = {r["term"] for r in rows if r["term"] == "pc"}
    assert computer_group == {"pc"}
    group = next(r["group_id"] for r in rows if r["term"] == "pc")
    assert {"computer", "laptop", "notebook"} <= {r["term"] for r in rows if r["group_id"] == group}
    assert not any(r["term"].startswith("#") for r in rows)


def test_expand_query_adds_synonyms_and_keeps_the_question():
    with connect() as conn:
        expanded = expand_query("My PC is really slow", conn)
    assert expanded.startswith("My PC is really slow")
    assert "computer" in expanded and "laptop" in expanded and "sluggish" in expanded


def test_expand_query_matches_plurals_and_phrases():
    with connect() as conn:
        assert "copier" in expand_query("The printers are broken", conn)
        assert "meeting" in expand_query("No sound in my video call", conn)


def test_expand_query_leaves_unrelated_questions_alone():
    with connect() as conn:
        assert expand_query("What is the capital of France?", conn) == "What is the capital of France?"


def test_synonyms_find_an_article_the_plain_words_miss():
    plain = search("My notebook is sluggish", synonyms=False)
    with_synonyms = search("My notebook is sluggish", synonyms=True)
    assert with_synonyms and with_synonyms[0]["slug"] == "slow-computer"
    assert not plain or plain[0]["slug"] != "slow-computer"


def test_synonyms_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("USE_SYNONYMS", "0")
    off = [r["slug"] for r in search("My notebook is sluggish")]
    assert off == [r["slug"] for r in search("My notebook is sluggish", synonyms=False)]
