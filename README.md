# Data question agent

A small FastAPI service that answers plain-English questions about an online store's database. A language model writes a SQL query, the service runs it safely, shows the model the rows or the error, lets it fix mistakes, and returns the answer together with the exact SQL and the rows behind it. The part that is built and measured here is the safety layer: what the agent is allowed to run, how long it may loop, and what it may say.

Status: tested offline. The Gemini backend is written but **has not been run**, because no API key was available while building, so how often the model writes the right SQL is **not measured**. Everything measured below checks the guardrails and the scoring harness, using a backend that replays the known correct query (it is not a language model).

## What it has

- **API:** `POST /ask` takes `question` (1 to 2000 characters) and an optional `session_id` for short chat memory. It returns `answer`, `refused`, `category`, `sql`, `columns`, `rows`, `truncated`, `steps`, `queries_run` and `queries_blocked`. `GET /health` names the backend. Malformed requests return 422. A refusal is a normal 200 response with `refused: true` and a `category`.
- **A sample database:** `data/store.db`, a made-up online store (200 customers, 40 products, 1,500 orders, 3,807 order lines over 2023 and 2024), built the same way every time by `data/build_db.py`. The `customers.email` column is private.
- **A bounded loop** (`app/agent.py`): the model replies with one move at a time (run a query, answer, or refuse), sees the rows or the error of each query, and may fix a failing query. It has 4 steps, then the request is refused. An answer is only accepted after at least one query ran successfully.
- **Three layers around the SQL** (`app/sqlguard.py`):
  1. a static check: one statement, starting with `SELECT` or `WITH`, at most 2,000 characters;
  2. a read-only database connection with an authorizer that allows only reads of the four store tables, hides `customers.email` and blocks `load_extension` and similar functions;
  3. limits of 50 rows returned and 2 seconds of running time.
- **Question and answer checks** (`app/guardrails.py`): empty, over 500 characters, control characters and prompt-injection patterns ("ignore previous instructions", asking for the system prompt or an API key) are refused before the model is called. An answer is withheld if it contains something that looks like an API key or an email address.
- **Model backends** (`app/llm.py`): Gemini over its REST API (`gemini-3.5-flash-lite` by default, set with `GEMINI_MODEL`, key in `GEMINI_API_KEY`), with a system prompt that lists the tables without the private column. Without a key the service starts, and `/ask` returns a `model_error` refusal.
- **Chat memory:** in memory only, 6 turns per session and 1,000 sessions.

## Evaluation

`eval/questions.py` holds three sets, and `eval/run_eval.py` runs them and writes `eval/results.json`.

**1. Questions (40).** Each has a known correct SQL query, in five kinds: 13 simple, 8 aggregate, 12 join, 5 date and 2 subquery. A question passes when the agent's final query returns the same rows as the correct one (floats rounded to 2 decimals, row order and column names ignored). A "lenient" score also accepts extra columns.

| Offline run, replaying the correct query | Result |
|---|---|
| Questions answered, none refused | 40 of 40 |
| Same rows as the correct query (strict and lenient) | 40 of 40 |
| Correct queries blocked by a guardrail | 0 of 40 |

This shows that the loop, the guardrails and the scorer work together and that no correct query is wrongly blocked. It says nothing about a language model, because the backend here is given the answers. The scorer itself is covered by tests, including cases where it must say "no".

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

- **Answer quality is unmeasured.** No language model has been run, so there is no accuracy figure for model-written SQL. Run with a key, the same script gives one.
- **Paraphrased injections get past the input check.** Those 3 questions would reach the model. Even then, the SQL layers still stop writes and reads of the email column, but a model could still be talked into other harmless-but-off-topic behaviour.
- **The questions and attacks were written by one person who knew the database,** so the questions use clear wording and the attacks are the ones that person thought of. The numbers are for this set, not for a general attack surface.
- **Questions that ask for changes** ("delete all orders") rely on the model choosing to refuse. If it tries the write instead, the SQL layers stop it, but this path is only tested with a scripted model.
- The email check on answers looks for the shape of an address, so it would also withhold a made-up address that appears in a question's own text.
- One small SQLite database with made-up data. No authentication, rate limiting or deployment, and no real users.
- The `Dockerfile` has not been built, because the Docker daemon was not running.

## Repository

| Path | Contents |
|---|---|
| `app/main.py` | FastAPI app and the request and response models |
| `app/agent.py` | The bounded loop and the session memory |
| `app/sqlguard.py` | The three SQL safety layers and the schema text for the prompt |
| `app/guardrails.py` | Question and answer checks, limits |
| `app/llm.py` | Gemini backend, the replay backend used for evaluation, and the no-model backend |
| `data/build_db.py`, `data/store.db` | The sample database and the script that builds it |
| `eval/` | The question, attack and injection sets, the evaluation script and its results |
| `tests/` | 70 tests: SQL safety (including every attack), guardrails, the loop with scripted models, the scorer, the build and the API |
