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
    # Several chunks tie on score here, so assert it is found, not that it is first.
    assert "slow-computer" in [r["slug"] for r in with_synonyms]
    assert not plain or plain[0]["slug"] != "slow-computer"


def test_synonyms_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("USE_SYNONYMS", "0")
    off = [r["slug"] for r in search("My notebook is sluggish")]
    assert off == [r["slug"] for r in search("My notebook is sluggish", synonyms=False)]


def test_every_article_has_a_title_and_sections():
    paths = sorted(ARTICLES_DIR.glob("*.md"))
    assert len(paths) >= 30
    for path in paths:
        chunks = parse_article(path)
        assert chunks, f"{path.name} has no sections"
        assert all(c["title"] and c["heading"] and c["content"] for c in chunks), path.name


def test_eval_questions_point_to_existing_articles():
    import json
    from pathlib import Path

    slugs = {p.stem for p in ARTICLES_DIR.glob("*.md")}
    for file in sorted(Path("eval").glob("questions*.json")):
        for item in json.loads(file.read_text(encoding="utf-8")):
            if item["expected"]:
                assert item["expected"] in slugs, f"{file.name}: unknown article {item['expected']}"


def test_answer_eval_parses_judge_reply_and_grades_with_fakes():
    from eval.run_answer_eval import grade_item, parse_verdict, summarize

    v = parse_verdict('Sure! {"grounded": true, "relevant": false, "refused": true, "reason": "x"}')
    assert v["grounded"] and not v["relevant"] and v["refused"]

    ok = grade_item(
        {"q": "I forgot my password", "expected": "password-reset"},
        answer_fn=lambda q, p: "Use the self-service reset.",
        judge_fn=lambda prompt: '{"grounded": true, "relevant": true, "refused": false, "reason": "ok"}',
    )
    nothing = grade_item({"q": "zzzz qqqq", "expected": None}, answer_fn=None, judge_fn=None)
    assert ok["calls"] == 2 and nothing["refused"] and nothing["calls"] == 0
    s = summarize([ok, nothing])
    assert s["grounded"] == 1.0 and s["correct_refusal"] == 1.0
    assert ok["in_context"] and s["in_context"] == 1 and s["answered_when_in_context"] == 1.0


def test_message_text_skips_thinking_blocks():
    from types import SimpleNamespace as NS

    from app.answer import message_text

    msg = NS(content=[NS(type="thinking", thinking="hmm"), NS(type="text", text="Restart it.")])
    assert message_text(msg) == "Restart it."
    with pytest.raises(ValueError):
        message_text(NS(content=[NS(type="thinking", thinking="hmm")]))


def test_build_context_uses_whole_top_articles(monkeypatch):
    from app.answer import build_context

    passages = search("My Bluetooth headphones won't connect to my laptop")
    monkeypatch.delenv("ANSWER_CONTEXT", raising=False)
    full = build_context(passages)
    assert "Pairing a device" in full  # the section that only the full article brings
    monkeypatch.setenv("ANSWER_CONTEXT", "passages")
    assert full.count("[") >= build_context(passages).count("[")


# ---- Spanish support -------------------------------------------------------
def test_detect_language_and_accents():
    from app.lang import detect_language, strip_accents

    assert detect_language("I forgot my password") == "en"
    assert detect_language("Olvidé mi contraseña") == "es"
    assert detect_language("mi impresora no imprime") == "es"
    assert detect_language("wifi") == "en"  # unknown text counts as English
    assert strip_accents("Conexión inalámbrica") == "Conexion inalambrica"


def test_every_english_article_has_a_spanish_twin_with_the_same_slug():
    from app.ingest import SOURCES

    (_, en_dir, _), (_, es_dir, _) = SOURCES
    assert {p.stem for p in en_dir.glob("*.md")} == {p.stem for p in es_dir.glob("*.md")}


def test_spanish_question_is_answered_from_spanish_articles():
    results = search("Mi impresora aparece desconectada y no imprime")
    assert results and results[0]["slug"] == "printer-not-working"
    assert all(r["lang"] == "es" for r in results)
    english = search("My printer is offline")
    assert english and all(r["lang"] == "en" for r in english)


def test_spanish_search_ignores_accents():
    with_accents = search("¿Cómo restablezco mi contraseña olvidada?")
    without = search("como restablezco mi contrasena olvidada")
    assert with_accents and without
    assert with_accents[0]["slug"] == without[0]["slug"] == "password-reset"


def test_spanish_synonyms_only_apply_to_spanish():
    from app.search import expand_query

    with connect() as conn:
        assert "ordenador" in expand_query("mi compu está lenta", conn, "es")
        assert "ordenador" not in expand_query("my pc is slow", conn, "en")


def test_ask_answers_no_match_in_the_language_of_the_question():
    client = TestClient(app)
    data = client.post("/ask", json={"question": "zzzz qqqq xxxx ¿qué es eso?"}).json()
    assert data["mode"] == "no_match" and "No encontré" in data["answer"]


def test_home_page_gets_the_contact_email_from_the_environment(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    assert 'var CONTACT_EMAIL = "";' in client.get("/").text
    monkeypatch.setenv("CONTACT_EMAIL", "hola@example.com")
    assert 'var CONTACT_EMAIL = "hola@example.com";' in client.get("/").text
    monkeypatch.setenv("CONTACT_EMAIL", 'x";alert(1);//')  # not an email: ignored
    assert "alert(1)" not in client.get("/").text
