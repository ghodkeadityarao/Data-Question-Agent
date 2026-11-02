# Data question agent

A small web app and FastAPI service that answers plain-English questions about an online store's database. A language model writes a SQL query, the service runs it safely, shows the model the rows or the error, lets it fix mistakes, and returns the answer together with the exact SQL and the rows behind it. The part that is built and measured here is the safety layer: what the agent is allowed to run, how long it may loop, and what it may say.

Status: working and tested. On the 40 evaluation questions, `gemini-3.5-flash-lite` wrote SQL that returned the correct rows for **38 of 40 (95%)** in one run, with every dangerous query stopped by the guardrails (25 of 25). The service is not deployed yet.

## What it has

- **A chat page** (`static/`, served at `/`): starter questions, then for each reply the answer, the SQL the model wrote in a code block, and the rows in a table. Refusals show their reason (for example `injection`, `declined`, `limit`). It also lists the database tables, uses dark and light themes that follow the device, and works on phones. It is plain HTML, CSS and JavaScript with no build step, writes replies with `textContent` only, and a content security policy blocks inline scripts and styles.
- **API:** `POST /ask` takes `question` (1 to 2000 characters) and an optional `session_id` for short chat memory. It returns `answer`, `refused`, `category`, `sql`, `columns`, `rows`, `truncated`, `steps`, `queries_run` and `queries_blocked`. `GET /schema` lists the tables (without the private column) and `GET /health` names the backend. Malformed requests return 422. A refusal is a normal 200 response with `refused: true` and a `category`.
- **A sample database:** `data/store.db`, a made-up online store (200 customers, 40 products, 1,500 orders, 3,807 order lines over 2023 and 2024), built the same way every time by `data/build_db.py`. The `customers.email` column is private.
- **A bounded loop** (`app/agent.py`): the model replies with one move at a time (run a query, answer, or refuse), sees the rows or the error of each query, and may fix a failing query. It has 4 steps, then the request is refused. An answer is only accepted after at least one query ran successfully.
- **Three layers around the SQL** (`app/sqlguard.py`):
  1. a static check: one statement, starting with `SELECT` or `WITH`, at most 2,000 characters;
  2. a read-only database connection with an authorizer that allows only reads of the four store tables, hides `customers.email` and blocks `load_extension` and similar functions;
  3. limits of 50 rows returned and 2 seconds of running time.
- **Question and answer checks** (`app/guardrails.py`): empty, over 500 characters, control characters and prompt-injection patterns ("ignore previous instructions", asking for the system prompt or an API key) are refused before the model is called. An answer is withheld if it contains something that looks like an API key or an email address.
- **Model backends** (`app/llm.py`): Gemini over its REST API (`gemini-3.5-flash-lite` by default, set with `GEMINI_MODEL`, key in `GEMINI_API_KEY`), with a system prompt that lists the tables without the private column. Without a key the service starts, and `/ask` returns a `model_error` refusal.
- **Protection for the model key:** 15 questions per minute per network address (set `REQUESTS_PER_MINUTE`) and a shared budget of 1,000 questions per day (`QUESTIONS_PER_DAY`), both answered with a clear 429 message. Invalid requests do not count. Behind a proxy (`TRUST_PROXY=1`, the Docker default) the address the proxy added to `X-Forwarded-For` is used.
- **Docker image:** `python:3.12-slim`, runs as a non-root user, listens on the `PORT` the host provides (8000 by default).
- **Chat memory:** in memory only, 6 turns per session and 1,000 sessions.

## Evaluation

`eval/questions.py` holds three sets, and `eval/run_eval.py` runs them and writes `eval/results.json`.

**1. Questions (40).** Each has a known correct SQL query, in five kinds: 13 simple, 8 aggregate, 12 join, 5 date and 2 subquery. A question passes when the agent's final query returns the same rows as the correct one (floats rounded to 2 decimals, row order and column names ignored). A "lenient" score also accepts extra columns.

| One run of `gemini-3.5-flash-lite`, temperature 0 | Result |
|---|---|
| Same rows as the correct query, lenient (extra columns allowed) | 38 of 40 (95%) |
| Same rows as the correct query, strict | 37 of 40 (92%) |
| Questions that failed because the model call failed | 0 of 40 |
| Queries stopped by a guardrail during the run | 0 |
| Average steps per question | 2.02 (39 questions needed one query, 1 needed two) |

By kind (lenient): simple 13 of 13, aggregate 8 of 8, date 5 of 5, subquery 2 of 2, join 10 of 12.

The three that were not an exact match:
- **Cheapest Electronics product (strict only):** the model returned the product name and also its price. The rows are right with an extra column, so it counts as correct under lenient scoring.
- **Top 5 products by units sold, and units of Sports products sold (wrong under both scores):** the model excluded cancelled and returned orders from "sold", and the answer key counts every order line. Its answer said so ("excluding cancelled and returned orders"). Those two questions are ambiguous, and I did not change the answer key after seeing the result.

With 40 questions, 38 correct is an estimate with a wide margin: a 95% interval of roughly 84% to 99%.

A second check ran the same harness with a backend that replays the known correct query (it is not a language model): 40 of 40 pass and no correct query is blocked, which shows that the loop, the guardrails and the scorer work together. The scorer is also covered by tests, including cases where it must say "no".

**2. Attacks (25 queries).** 12 try to write or escape (`DROP`, `DELETE`, `UPDATE`, `INSERT`, a second statement, a comment in front, a delete hidden behind `WITH`, `ATTACH`, `VACUUM INTO`, `PRAGMA`), 9 try to read private data (the email column directly, through `SELECT *`, an alias, a subquery, `sqlite_master`, `pragma_table_info`), 2 call file or extension functions and 2 never finish.

| | Guardrails | Plain read-write SQLite connection |
|---|---|---|
| Attacks stopped | 25 of 25 | 4 of 23 (19 ran without error) |
| Database file changed | no | 10 of the 12 write attacks ran |
| Private email column returned | no | 9 of 9 private reads ran |

The plain connection is the same SQLite with no checks, run on a throwaway copy of the database. "Ran without error" is the measure, and the 2 slow attacks were not run on it because they would not finish. The guardrails stopped the 25 attacks in four ways: 12 by the static check, 11 by the authorizer, 1 by the time limit and 1 by SQLite itself (calling a function that does not exist). The database file had the same hash before and after.

**3. Injection questions (11).**

| | Blocked by the input check |
|---|---|
| Obvious phrasing (8) | 8 of 8 |
| Paraphrased to avoid the patterns (3) | 0 of 3 |

## Limits

- **One run, one model.** The 95% is a single run at temperature 0 of one model on 40 questions, so it is not a stable rate. Another model, a rewording of the questions or a different database could score quite differently, and speed was not measured. The system prompt also tells the model that cancelled and returned orders are in the table, which may have led it to exclude them.
- **Paraphrased injections get past the input check.** Those 3 questions would reach the model. Even then, the SQL layers still stop writes and reads of the email column, but a model could still be talked into other harmless-but-off-topic behaviour.
- **The questions and attacks were written by one person who knew the database,** so the questions use clear wording and the attacks are the ones that person thought of. The questions were not tuned after seeing the model's answers. The numbers are for this set, not for a general attack surface.
- **Questions that ask for changes** ("delete all orders") rely on the model choosing to refuse. If it tries the write instead, the SQL layers stop it, but this path is only tested with a scripted model.
- The email check on answers looks for the shape of an address, so it would also withhold a made-up address that appears in a question's own text.
- One small SQLite database with made-up data. No login, no deployment yet and no real users. The rate limit and daily budget live in one process's memory, so they reset on a restart and need a single worker.
- **What was checked in Docker:** the image builds (277 MB), starts as a non-root user on a custom `PORT`, serves the page with its security headers, answers `/health`, `/schema` and `/ask`, and returns 429 on the 16th request in a minute. The page was viewed in a browser at desktop and phone width with a scripted stand-in model, so the page and the container have not been tried together with live Gemini answers.

## Repository

| Path | Contents |
|---|---|
| `app/main.py` | FastAPI app and the request and response models |
| `app/agent.py` | The bounded loop and the session memory |
| `app/sqlguard.py` | The three SQL safety layers and the schema text for the prompt |
| `app/guardrails.py` | Question and answer checks, limits |
| `app/limits.py` | Per-address rate limit and daily budget |
| `static/` | The chat page: HTML, styles and script |
| `app/llm.py` | Gemini backend, the replay backend used for evaluation, and the no-model backend |
| `data/build_db.py`, `data/store.db` | The sample database and the script that builds it |
| `eval/` | The question, attack and injection sets, the evaluation script and its results |
| `tests/` | 80 tests: SQL safety (including every attack), guardrails, the loop with scripted models, the scorer, the build, the page and its headers, the limits and the API |
