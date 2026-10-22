# Support KB Assistant

**Live demo:** https://support-kb-assistant.onrender.com (free instance: the first request can take up to a minute to wake up)

A small web app and API that answers IT support questions from a knowledge base, with **citations**, a **measured retrieval quality**, and a log that shows **which questions the knowledge base cannot answer**. Built with FastAPI and PostgreSQL. Claude can optionally write the final answer.

I built it to connect my IT support background (Google IT Support certificate) with my database work (PostgreSQL), and to practice retrieval and evaluation, not only "calling an LLM".

## What it does

- Answers a question with the best matching passage and shows the articles it came from. If nothing is relevant enough, it says so instead of guessing.
- Works in **English and Spanish**: it detects the language of the question and answers from the articles written in that language (the same 30 articles exist in both).
- Understands that "PC", "notebook" and "computer" (or "compu", "notebook" and "computadora") mean the same thing, using synonym tables stored in PostgreSQL.
- Stores every question and a "Was this helpful?" vote, and reports the most frequent unanswered questions. In support, each of them is an article that should be written.
- Has a simple web page, so it can be tried without knowing anything about APIs.

## How it works

```
question -> detect language -> expand with synonyms -> PostgreSQL full-text search (weighted, ranked) -> top passages
         -> answer: best passage (default) or Claude, grounded only on those passages
         -> response with sources, or "no match"; the question is logged either way
```

- **Chunking:** each article in `data/articles/` (English) and `data/articles_es/` (Spanish, same file names) is split by `##` section, so results are specific passages.
- **Language** (`app/lang.py`): a small word-based detector picks English or Spanish (no dependencies). Each language uses its own PostgreSQL stemmer, and Spanish is searched without accents, because people often type without them.
- **Retrieval** (`app/search.py`): the question becomes an OR query over stemmed keywords, ranked with `ts_rank_cd` (title > heading > body).
- **Synonyms** (`data/synonyms.txt` and `data/synonyms_es.txt`): groups of equivalent words are loaded into a table by `app/ingest.py`. If a question contains a word of a group (plurals and multi-word terms included), the rest of the group is added to the search.
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
| `CONTACT_EMAIL` | Optional: fills the "Get in touch" button of the page, so the address is not in the source code |

## Evaluation

Six question sets (four in English, two in Spanish), written in different words than the articles. Each has answerable questions (with the expected article) and out-of-scope ones.

| Set | Questions | Role |
|---|---|---|
| `dev` | 20 + 4 | Used to choose the score threshold |
| `heldout` | 14 + 4 | Written separately, but I had seen its failures when I wrote the synonyms |
| `test` | 16 + 6 | Written **before** the synonyms and run only once, at the end |
| `test_v2` | 32 + 8 | Written **before** the 16 articles that doubled the knowledge base (14 to 30), run once after freezing everything |
| `es_dev` | 30 + 6 | Spanish. Used to write the Spanish synonyms |
| `es_test` | 30 + 8 | Spanish. Written **before** the Spanish articles and synonyms; see below for what happened when I ran it |

```bash
python -m eval.run_eval --verbose
```

Knowledge base with 14 articles:

| Set | Synonyms | hit@1 | hit@3 | MRR | Abstain |
|---|---|---|---|---|---|
| dev | off | 0.85 | 0.90 | 0.89 | 0.75 |
| dev | on | 0.95 | 1.00 | 0.97 | 0.75 |
| heldout | off | 0.36 | 0.79 | 0.52 | 0.25 |
| heldout | on | 0.64 | 0.93 | 0.77 | 0.25 |
| test | off | 0.69 | 0.94 | 0.80 | 0.33 |
| test | on | 0.88 | 1.00 | 0.94 | 0.33 |

Same questions after growing the knowledge base to 30 articles, plus the new blind set:

| Set | Synonyms | hit@1 | hit@3 | MRR | Abstain |
|---|---|---|---|---|---|
| dev | off | 0.85 | 0.95 | 0.88 | 0.75 |
| dev | on | 0.90 | 1.00 | 0.94 | 0.75 |
| heldout | off | 0.36 | 0.43 | 0.46 | 0.25 |
| heldout | on | 0.64 | 0.86 | 0.72 | 0.25 |
| test | off | 0.50 | 0.81 | 0.65 | 0.17 |
| test | on | 0.88 | 1.00 | 0.94 | 0.17 |
| **test_v2** | off | 0.84 | 0.94 | 0.89 | 0.12 |
| **test_v2** | on | **0.84** | **1.00** | **0.92** | 0.12 |

Spanish (30 articles in Spanish, 182 passages in total):

| Set | Synonyms | hit@1 | hit@3 | MRR | Abstain |
|---|---|---|---|---|---|
| es_dev | off | 0.77 | 0.90 | 0.83 | 0.50 |
| es_dev | on | 0.83 | 0.93 | 0.89 | 0.50 |
| es_test, first run (blind) | off | 0.70 | 0.87 | 0.79 | 0.38 |
| es_test, first run (blind) | on | 0.90 | 0.93 | 0.92 | 0.25 |
| es_test, after the fix below | off | 0.77 | 0.93 | 0.84 | 0.38 |
| es_test, after the fix below | on | 0.93 | 0.97 | 0.95 | 0.25 |

**What happened with `es_test`.** Its first run was blind, and it showed failures that made no sense (for example "Olvidé mi contraseña de la cuenta de trabajo" did not find the password article). Looking into them I found a bug: the search stemmed the words twice, which is harmless in English but breaks Spanish ("contrasen" became "contr"). I fixed it and ran `es_test` again, so the second run is **not blind any more**; I report both. The English results did not change with the fix.

The language detector classified all 178 questions of the six sets correctly, but I wrote those questions myself, so that is an easy test.

hit@1 / hit@3: the expected article is the first result / among the first three. MRR: mean reciprocal rank. Abstain: share of out-of-scope questions correctly answered with "no match".

### What the results say

- **Synonyms help.** On the clean `test` set, the right article is the first result 88% of the time, against 69% without synonyms.
- **My first numbers were optimistic.** Without synonyms, hit@1 goes from 0.85 on `dev` to 0.36 on `heldout`: the first set was too close to the wording of the articles.
- **A bigger knowledge base makes plain search worse.** With 30 articles instead of 14, hit@3 without synonyms on `heldout` drops from 0.79 to 0.43 and hit@1 on `test` from 0.69 to 0.50: more articles share words, so unrelated ones outrank the right one. With synonyms the `test` numbers do not change (0.88 / 1.00).
- **On new topics the result holds.** On `test_v2`, written before the new articles, the right article is among the first three 100% of the time with synonyms (94% without). hit@1 is 0.84 either way, so on these questions synonyms mostly help the right article reach the top three.
- **Refusing off-topic questions got worse.** "How do I reset my Netflix password?" still matches the password article, and a score threshold cannot tell the difference. With 30 articles more out-of-scope questions share words with some article ("Can you recommend a good monitor to buy?" matches the monitor article), and abstention fell to 12% on `test_v2`. I added synonym groups for the new topics after seeing how the old sets behaved, but I wrote the `test_v2` questions knowing the topic list, so treat its numbers as slightly optimistic.
- **Synonyms add some noise.** In `heldout`, "I left my laptop on the train" dropped from first to second place, because "laptop" also pulls in articles about slow computers.
- **Spanish works about as well as English, but refuses worse.** With synonyms, first-result accuracy is 0.83 to 0.93 on the Spanish sets, but only 25% of out-of-scope questions are rejected by search alone (English `test_v2`: 12%, `test`: 17%, so both are weak). Frequent Spanish words such as "equipo", "cuenta" and "trabajo" appear in many articles and pull in the wrong ones.
- **The sets are small.** One question is 6 to 7 points of hit@k, and I wrote both the synonyms and the questions, so treat the differences as an indication, not a proof.

### Evaluating the answers written by Claude

Retrieval is only half of it. `eval/run_answer_eval.py` retrieves passages, lets Claude answer, and then uses Claude as a judge to check that the answer is **grounded** in the passages, **relevant**, and that out-of-scope questions are **refused**. It needs `ANTHROPIC_API_KEY` and costs about one dollar for 62 questions (the `test` and `test_v2` sets).

```bash
python -m eval.run_answer_eval --dry-run          # counts API calls, no key needed
python -m eval.run_answer_eval --verbose --save answers.json
python -m eval.review_answers answers.json        # prints the cases worth a human look
```

Claude first received the 3 best passages; then I changed it to receive every section of the 2 best articles (`ANSWER_CONTEXT=passages` brings back the old behavior). Same 62 questions, same judge:

| | 3 best passages | Whole top 2 articles |
|---|---|---|
| Answerable questions | 48 | 48 |
| Answers fully grounded in the passages (judge) | 0.94 | 0.94 |
| The right article among the 3 best passages | 45 of 48 | 45 of 48 |
| Claude answered when it was | 0.80 | **0.91** |
| Out-of-scope questions refused | 14 of 14 | 14 of 14 |

I made that change after reading the failures of the first run, so the improvement is measured on questions I had already seen. It is an indication, not a clean test.

What I found when I read the cases by hand:

- **Claude backs up retrieval where retrieval is weak.** Search alone refused only 2 of 14 out-of-scope questions, but Claude refused the other 12 ("Can you recommend a good monitor to buy?", "How do I delete my Facebook account?"). Nothing out of scope was answered.
- **The 3 "not grounded" cases are small.** Two objections were to the closing "open a ticket if this does not help", one to a sentence about external disks, one to naming a chat status. None invents a procedure.
- **The 9 "declined" cases are mostly partial refusals** ("the passages do not cover X", followed by the related steps). In several the cause is retrieval at section level: the right article arrived, but not the section with the answer (for example "Adding the account" for "How do I get my work email on my phone?"). Claude only saw the 3 best passages, so I changed it to receive the whole article, which raised the share of answered questions from 0.80 to 0.91 without hurting refusals. In 2 of the 3 questions where the right article was not among the 3 best passages, the whole article let Claude answer correctly.
- **Limits.** The same model answers and judges, there are only 62 questions, and "relevant" also counts a well explained refusal as relevant, so I do not use that number as a result.

### Things I tried that did not work

I also tested hybrid search with `pgvector`, averaging pre-trained word vectors into embeddings and merging both rankings. It did not beat plain full-text search on questions it had not seen (hit@3 0.64 against 0.79 on `heldout`), so I did not keep it. That experiment is not part of this repository.

## Data and privacy

Questions are stored so the knowledge base can improve: the text, the time, whether it was answered, the best article and the vote. No name, IP address or other data of the visitor is stored. The page tells visitors not to type personal information, and `/admin/report` is protected with a secret token.

## Deploy

Render (web service) and Neon (PostgreSQL), both on free plans.

- Build command: `pip install -r requirements.txt && python -m app.ingest`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables: `DATABASE_URL` (Neon connection string), `ADMIN_TOKEN`, and optionally `ANTHROPIC_API_KEY`.

## Client documents (multi-tenant)

Besides the public demo articles, each client can have their own private knowledge base. Everything is separated by client: documents, search, answers, the question log and the report.

Create a client (the API key is shown once; only its SHA-256 hash is stored):

```
python -m app.tenants create "Acme IT Services"
python -m app.tenants list
python -m app.tenants delete <id>      # removes the client and all their data
```

The client sends the key in the `X-API-Key` header:

| Endpoint | What it does |
|---|---|
| `POST /tenant/documents` | Upload one or more `.md`, `.txt`, `.pdf` (with selectable text) or `.docx` files (multipart field `files`). Same file name replaces the old version. |
| `GET /tenant/documents` | List the client's documents. |
| `DELETE /tenant/documents/{slug}` | Delete one document. |
| `POST /ask`, `GET /search` | With the key, they use only that client's documents. Without it, the public demo. A wrong key returns 401 (it never falls back to another knowledge base). |
| `POST /feedback` | Scoped to the client's own questions. |
| `GET /tenant/report` | Unanswered questions and usage of that client only. |

Example:

```
curl -H "X-API-Key: kb_..." -F "files=@vpn-guide.pdf" -F "files=@onboarding.docx" https://YOUR-APP/tenant/documents
curl -H "X-API-Key: kb_..." -H "Content-Type: application/json" -d '{"question":"How do I connect to the VPN?"}' https://YOUR-APP/ask
```

How documents are processed: text is extracted, then split by heading (markdown `##` or Word heading styles) or, when there are none, into paragraphs of about 150-250 words. The language of each document (English or Spanish) selects the stemmer. Limits: 5 MB per file, 50 documents per client. Scanned PDFs (images) are not supported (no OCR yet).

Deploys are safe: `python -m app.ingest` reloads only the public articles and never touches client documents.

Not built yet: a web panel for clients, an embeddable chat widget, rate limiting, OCR, and the evaluation of answers on a client's own documents (the retrieval numbers above are for the demo articles only).

## Usage limits

Each question to the public demo can cost a call to Claude, so `/ask` and `/search` are limited (HTTP 429 with a `Retry-After` header):

| Who | Per minute | Per day | Environment variables |
|---|---|---|---|
| Anonymous visitor (by IP) | 10 | 100 | `ANON_PER_MINUTE`, `ANON_PER_DAY` |
| The whole public demo | | 1000 | `ANON_GLOBAL_PER_DAY` |
| A client with an API key | 30 | 1000 | `TENANT_PER_MINUTE`, `TENANT_PER_DAY` |
| Uploads per client | | 60 per hour | `UPLOADS_PER_HOUR` |

Set a variable to `0` to remove that limit. The counters live in memory: they reset on every deploy or restart and are not shared between servers, which is enough for one small server. The visitor's IP is read from `X-Forwarded-For`; since a client can fake that header, the global daily cap is what really protects the bill.

## Tests and CI

```bash
pytest -q
```

56 tests cover parsing, search in both languages, language detection, synonyms, the API, the question log, feedback, the admin report, usage limits and client separation (one client must never see another's documents). They re-run `python -m app.ingest`, which reloads the articles of the database they connect to, so use a local database. Rows created by the tests are removed afterwards.

CI runs the tests and the evaluation on every push (`.github/workflows/ci.yml`).

## Limitations and next steps

- The knowledge base is small: 30 articles, and 14 to 32 answerable questions per evaluation set.
- Refusing out-of-scope questions is weak (see above). A relevance check on the generated answer, or a classifier, could help.
- No rate limiting: someone could fill the question log.
- Next: write Spanish stop-word and synonym lists tuned on more questions, improve how out-of-scope questions are rejected, add articles guided by the unanswered-questions report, grow the evaluation sets (ideally with questions written by someone else), and try a real sentence-embedding model for semantic search.
