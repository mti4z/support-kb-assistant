# Support KB Assistant

A small API that answers IT support questions from a knowledge base, with **citations** and a **measured retrieval quality**. Built with FastAPI and PostgreSQL full-text search. Claude can optionally write the final answer.

I built it to connect my IT support background (Google IT Support certificate) with my database work (PostgreSQL), and to practice retrieval and evaluation, not only "calling an LLM".

## How it works

```
question -> PostgreSQL full-text search (weighted tsvector, ranked) -> top passages
         -> answer: best passage (default) or Claude, grounded only on those passages
         -> response with sources, or "no match" when nothing is relevant enough
```

- **Chunking:** each article in `data/articles/` is split by `##` section, so results are specific passages.
- **Retrieval:** natural language questions are turned into an OR query over stemmed keywords and ranked with `ts_rank_cd` (title > heading > body weights) in `app/search.py`.
- **Abstaining:** below a score threshold the API says it does not know instead of guessing.
- **Generation (optional):** set `ANTHROPIC_API_KEY` and Claude answers using only the retrieved passages. Without a key, or if the call fails, it falls back to returning the best passage.

## Run it

```bash
docker compose up -d db
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/supportkb
python -m app.ingest
uvicorn app.main:app --reload
```

```bash
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "I cannot connect to the VPN from home"}'
```

Endpoints: `GET /health`, `GET /search?q=...`, `POST /ask`. Interactive docs at `/docs`.

## Evaluation

`eval/questions.json` has 24 questions written in different words than the articles: 20 answerable (with the expected article) and 4 out of scope.

```bash
python -m eval.run_eval --verbose
```

Current results (threshold 0.2):

| Metric | Value |
|---|---|
| hit@1 | 0.85 |
| hit@3 | 0.90 |
| MRR | 0.887 |
| Abstain accuracy (out of scope) | 0.75 |

CI runs the tests and the evaluation on every push (`.github/workflows/ci.yml`).

### Known limitations (what I learned)

- **Vocabulary mismatch.** "My PC is really slow" does not find the article about a slow *computer*, because keyword search does not know PC and computer mean the same. Same for "Teams meetings" vs "video calls".
- **Wrong abstain.** "How do I request vacation days?" matches the software *request* article on the word "request". A score threshold alone cannot fix this.
- The threshold was chosen looking at this same small set, so these numbers are optimistic. A larger held-out set is needed.

### Next steps

1. Add embeddings with `pgvector` and combine them with full-text search (hybrid retrieval). I expect this to fix the vocabulary mismatch cases, and I can verify it with the same eval.
2. Evaluate the generated answers too (groundedness, correct refusals), not only retrieval.
3. Grow the question set and keep a held-out part.

## Tests

```bash
pytest -q
```
