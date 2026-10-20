# Support KB Assistant

**Live demo:** https://support-kb-assistant.onrender.com (free instance: the first request can take up to a minute to wake up)

A small web app and API that answers IT support questions from a knowledge base, with **citations**, a **measured retrieval quality**, and a log that shows **which questions the knowledge base cannot answer**. Built with FastAPI and PostgreSQL. Claude can optionally write the final answer.

I built it to connect my IT support background (Google IT Support certificate) with my database work (PostgreSQL), and to practice retrieval and evaluation, not only "calling an LLM".

## What it does

- Answers a question with the best matching passage and shows the articles it came from. If nothing is relevant enough, it says so instead of guessing.
- Understands that "PC", "notebook" and "computer" mean the same thing, using a synonym table stored in PostgreSQL.
- Stores every question and a "Was this helpful?" vote, and reports the most frequent unanswered questions. In support, each of them is an article that should be written.
- Has a simple web page, so it can be tried without knowing anything about APIs.

## How it works

```
question -> expand with synonyms -> PostgreSQL full-text search (weighted, ranked) -> top passages
         -> answer: best passage (default) or Claude, grounded only on those passages
         -> response with sources, or "no match"; the question is logged either way
```

- **Chunking:** each article in `data/articles/` is split by `##` section, so results are specific passages.
- **Retrieval** (`app/search.py`): the question becomes an OR query over stemmed keywords, ranked with `ts_rank_cd` (title > heading > body).
- **Synonyms** (`data/synonyms.txt`): groups of equivalent words are loaded into a table by `app/ingest.py`. If a question contains a word of a group (plurals and multi-word terms included), the rest of the group is added to the search.
- **Abstaining:** below a score threshold the API says it does not know.
- **Generation (optional):** with `ANTHROPIC_API_KEY`, Claude answers using only the retrieved passages. Without a key, or if the call fails, it returns the best passage.
- **Question log** (`app/analytics.py`): each question is stored with whether it was answered, the best article and the user's vote. Logging never breaks an answer.

## Run it

```bash
docker compose up -d db
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/supportkb   # PowerShell: $env:DATABASE_URL="..."
python -m app.ingest        # loads the articles and the synonyms
uvicorn app.main:app --reload
```

Open http://localhost:8000. Or use the API:

```bash
curl -X POST localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "I cannot connect to the VPN from home"}'
```

| Endpoint | What it does |
|---|---|
| `GET /` | The web page |
| `POST /ask` | Answer a question, with sources and a `query_id` |
| `POST /feedback` | Save "was this helpful?" for a `query_id` |
| `GET /search?q=...` | Matching passages, without an answer |
| `GET /health` | Health check |
| `GET /admin/report` | Unanswered questions, answer rate, feedback. Needs the `X-Admin-Token` header |

Interactive docs at `/docs`.

### Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `ADMIN_TOKEN` | Secret for `/admin/report`. If it is not set, the endpoint does not exist (404) |
| `ANTHROPIC_API_KEY` | Optional: lets Claude write the answers |
| `USE_SYNONYMS` | Set to `0` to turn synonym expansion off |

## Evaluation

Three question sets, written in different words than the articles. Each has answerable questions (with the expected article) and out-of-scope ones.

| Set | Questions | Role |
|---|---|---|
| `dev` | 20 + 4 | Used to choose the score threshold |
| `heldout` | 14 + 4 | Written separately, but I had seen its failures when I wrote the synonyms |
| `test` | 16 + 6 | Written **before** the synonyms and run only once, at the end |

```bash
python -m eval.run_eval --verbose
```

| Set | Synonyms | hit@1 | hit@3 | MRR | Abstain |
|---|---|---|---|---|---|
| dev | off | 0.85 | 0.90 | 0.89 | 0.75 |
| dev | on | 0.95 | 1.00 | 0.97 | 0.75 |
| heldout | off | 0.36 | 0.79 | 0.52 | 0.25 |
| heldout | on | 0.64 | 0.93 | 0.77 | 0.25 |
| **test** | off | 0.69 | 0.94 | 0.80 | 0.33 |
| **test** | on | **0.88** | **1.00** | **0.94** | 0.33 |

hit@1 / hit@3: the expected article is the first result / among the first three. MRR: mean reciprocal rank. Abstain: share of out-of-scope questions correctly answered with "no match".

### What the results say

- **Synonyms help.** On the clean `test` set, the right article is the first result 88% of the time, against 69% without synonyms.
- **My first numbers were optimistic.** Without synonyms, hit@1 goes from 0.85 on `dev` to 0.36 on `heldout`: the first set was too close to the wording of the articles.
- **Refusing off-topic questions did not improve.** "How do I reset my Netflix password?" still matches the password article, and a score threshold cannot tell the difference.
- **Synonyms add some noise.** In `heldout`, "I left my laptop on the train" dropped from first to second place, because "laptop" also pulls in articles about slow computers.
- **The sets are small.** One question is 6 to 7 points of hit@k, and I wrote both the synonyms and the questions, so treat the differences as an indication, not a proof.

### Things I tried that did not work

I also tested hybrid search with `pgvector`, averaging pre-trained word vectors into embeddings and merging both rankings. It did not beat plain full-text search on questions it had not seen (hit@3 0.64 against 0.79 on `heldout`), so I did not keep it. That experiment is not part of this repository.

## Data and privacy

Questions are stored so the knowledge base can improve: the text, the time, whether it was answered, the best article and the vote. No name, IP address or other data of the visitor is stored. The page tells visitors not to type personal information, and `/admin/report` is protected with a secret token.

## Deploy

Render (web service) and Neon (PostgreSQL), both on free plans.

- Build command: `pip install -r requirements.txt && python -m app.ingest`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables: `DATABASE_URL` (Neon connection string), `ADMIN_TOKEN`, and optionally `ANTHROPIC_API_KEY`.

## Tests and CI

```bash
pytest -q
```

22 tests cover parsing, search, synonyms, the API, the question log, feedback and the admin report. They re-run `python -m app.ingest`, which reloads the articles of the database they connect to, so use a local database. Rows created by the tests are removed afterwards.

CI runs the tests and the evaluation on every push (`.github/workflows/ci.yml`).

## Limitations and next steps

- The knowledge base is small: 14 articles, and about 16 answerable questions per evaluation set.
- Refusing out-of-scope questions is weak (see above). A relevance check on the generated answer, or a classifier, could help.
- No rate limiting: someone could fill the question log.
- Next: add articles guided by the unanswered-questions report, grow the evaluation sets (ideally with questions written by someone else), evaluate the answers written by Claude (groundedness and correct refusals), and try a real sentence-embedding model for semantic search.
