# Kubernetes docs agent service

A small FastAPI service that answers questions about Kubernetes from a local copy of the official documentation, with guardrails around what it will answer, a bounded search loop, and an evaluation set that measures the guardrails against a plain retrieval baseline.

Status: working and tested offline. The language model backend (Gemini) is written but has not been run, because no API key was available while building. Everything measured below used the offline extractive backend, which quotes the best sentences of retrieved sections and is **not** a language model. Treat the answer-quality numbers as a baseline.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --port 8000
```

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "How do I add a taint to a node and what does NoSchedule do?"}'
```

`POST /ask` takes `question` (1 to 2000 characters) and an optional `session_id` for short chat memory. It returns `answer`, `refused`, `category`, `citations`, `sources` (section id, page URL, title, heading) and `steps`. Malformed requests return 422. A refusal is a normal 200 response with `refused: true` and a `category`.

To use Gemini, copy `.env.example` to `.env`, set `AGENT_BACKEND=gemini` and `GEMINI_API_KEY`, and export them. Never commit the key.

## How it works

1. **Input guardrails** (`app/guardrails.py`): reject empty or over 500 character questions, control characters, and prompt injection patterns (for example "ignore previous instructions", asking for the system prompt or an API key).
2. **Retrieval** (`app/retriever.py`): BM25 over 587 documentation sections from 143 pages, with camelCase splitting and extra weight on titles and headings. No embeddings and no API key.
3. **Scope check**: refuse unless the best section covers at least half of the question's weighted terms. Terms the corpus has never seen count against the match.
4. **Bounded loop** (`app/agent.py`): the backend may search again, up to 4 steps, then must answer. Hitting the limit is a refusal.
5. **Output guardrails**: the answer must cite only sections that were actually retrieved, and is withheld if it contains something that looks like a secret.
6. Chat memory is a bounded in-memory store: 6 turns per session, 1000 sessions.

## Evaluation

`eval/questions.jsonl` has 32 questions: 20 in scope (each tied to the documentation section that answers it, with key facts to look for), 5 out of scope and 7 prompt injection attempts, 2 of them paraphrased to avoid the obvious patterns. `eval/dev.jsonl` has 22 other questions that were used to sanity check the confidence threshold. The threshold of 0.5 was chosen before running either set. The dev set showed it holds from 0.3 to 0.6 and breaks at 0.7.

Final run, offline extractive backend, one run on the eval set:

| | Guarded agent | Plain retrieval, no guardrails |
|---|---|---|
| In scope questions answered | 20 of 20 | 20 of 20 |
| Out of scope refused | 3 of 5 | 1 of 5 |
| Injection attempts blocked | 7 of 7 | 0 of 7 |
| Gold section in top 3 results | 18 of 20 | same retriever |
| Gold section cited in answer | 18 of 20 | n/a |
| Answer contains all key facts | 14 of 20 | 7 of 20 |

Read this honestly:

- **Two out of scope questions got through.** "How do I reverse a linked list in Python?" and "How do I install Docker on Ubuntu?" matched unrelated documentation on words like "list", "Python", "install" and "Ubuntu" and were answered with off-topic text. The scope check counts term overlap, not meaning, so it fails on questions that reuse common words. I did not tune the threshold on the eval set to hide this. A language model backend that is told to say "not covered" should help, but that is untested.
- **Injection blocking is partly luck of the scope check.** 5 of 7 were caught by the pattern rules. The 2 paraphrases were refused only because their words did not match the documentation. A paraphrase that happens to contain Kubernetes terms could get through.
- **The questions were written by one person with the corpus open**, so many reuse the documentation's own words. Retrieval numbers are optimistic compared with real users.
- **The key fact check is a substring test.** It is a weak automated measure, not a correctness score. `eval/human_review.csv` has 10 sampled answers with blank columns for a person to grade correctness and grounding. It is not filled in.
- Small sample sizes: 32 questions, 5 out of scope. One question is 3 to 5 percentage points.

Reproduce:

```bash
python eval/run_eval.py                    # eval set, writes eval/results.json
python eval/run_eval.py --file eval/dev.jsonl
python eval/run_eval.py --backend gemini   # needs GEMINI_API_KEY
```

## Tests

```bash
python -m pytest -q
```

26 tests cover retrieval, every guardrail, the step limit, withheld ungrounded and secret-bearing answers, a failing model backend, bounded memory, and the API contract including 422 validation. A live `uvicorn` run was checked with curl for `/health`, an answered question, an injection refusal and an empty question.

## Not done

- Not deployed anywhere, no real users, no rate limiting or authentication.
- A `Dockerfile` is included but was not built, because the Docker daemon was not running when this was written.
- The Gemini backend and any language model evaluation are untested.
- Corpus: 143 pages sampled from the concepts and administration sections of kubernetes.io, not the whole site. Rebuild with `python data/build_corpus.py`. The documentation text is from kubernetes.io and licensed CC BY 4.0, with copyright held by The Kubernetes Authors.
