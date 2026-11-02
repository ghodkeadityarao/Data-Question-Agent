"""Evaluate the data agent and its guardrails.

    python eval/run_eval.py                    # offline: oracle backend (replays the correct SQL), no API key
    python eval/run_eval.py --backend gemini   # needs GEMINI_API_KEY; measures how often the model is right

Three parts:
  questions   40 questions with known correct SQL. Scored by execution: the agent's final query must return the same
              rows as the correct one (floats rounded to 2 decimals, row order ignored, column names ignored).
              "lenient" also accepts extra columns.
  attacks     25 dangerous queries a model might write, sent through the SQL guardrails and, for comparison, through a
              plain read-write SQLite connection on a throwaway copy of the database.
  injections  11 questions that try to change the agent's instructions, sent through the input check.
"""
import argparse
import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app import guardrails  # noqa: E402
from app.agent import Agent  # noqa: E402
from app.llm import GeminiLLM, OracleLLM  # noqa: E402
from app.sqlguard import run_query, schema_text  # noqa: E402
from eval.questions import ATTACKS, INJECTIONS, QUESTIONS  # noqa: E402

DB = os.path.join(ROOT, "data", "store.db")


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def norm(rows):
    def cell(v):
        return round(v, 2) if isinstance(v, float) else v
    return sorted((tuple(cell(v) for v in r) for r in rows), key=repr)


def matches(pred_rows, gold_rows):
    """(strict, lenient): same rows, or the same rows once extra columns are dropped."""
    gold = norm(gold_rows)
    if norm(pred_rows) == gold:
        return True, True
    if pred_rows and gold_rows and len(pred_rows[0]) > len(gold_rows[0]):
        n = len(gold_rows[0])
        for cols in itertools.combinations(range(len(pred_rows[0])), n):
            if norm([[r[c] for c in cols] for r in pred_rows]) == gold:
                return False, True
    return False, False


def run_questions(agent):
    out = []
    for kind, q, gold_sql in QUESTIONS:
        gold = run_query(DB, gold_sql)
        assert gold.ok, f"gold query failed: {gold_sql}"
        r = agent.ask(q)
        strict, lenient = (False, False) if r.refused else matches(r.rows, gold.rows)
        out.append({"kind": kind, "question": q, "refused": r.refused, "category": r.category, "strict": strict,
                    "lenient": lenient, "steps": r.steps, "queries_run": r.queries_run, "queries_blocked": r.queries_blocked,
                    "sql": r.sql, "answer": r.answer})
    return out


def summarize_questions(res):
    s = {"questions": len(res),
         "model_errors": pct(sum(r["category"] == "model_error" for r in res), len(res)),
         "refused_or_failed": pct(sum(r["refused"] for r in res), len(res)),
         "execution_accuracy_strict": pct(sum(r["strict"] for r in res), len(res)),
         "execution_accuracy_lenient": pct(sum(r["lenient"] for r in res), len(res)),
         "accuracy_on_questions_the_model_answered": pct(sum(r["lenient"] for r in res),
                                                         sum(r["category"] != "model_error" for r in res)),
         "mean_steps": round(sum(r["steps"] for r in res) / len(res), 2),
         "guardrail_blocks_during_run": sum(r["queries_blocked"] for r in res)}
    s["by_kind"] = {k: pct(sum(r["lenient"] for r in res if r["kind"] == k), sum(r["kind"] == k for r in res))
                    for k in sorted({r["kind"] for r in res})}
    return s


def file_hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_attacks():
    before = file_hash(DB)
    guarded = []
    for kind, sql in ATTACKS:
        res = run_query(DB, sql)
        layer = "allowed" if res.ok else ("authorizer" if "reads something" in res.error
                                          else "time limit" if "longer than" in res.error
                                          else "static check" if res.blocked else "database error")
        guarded.append({"kind": kind, "sql": sql, "stopped": not res.ok, "stopped_by": layer, "rows_returned": len(res.rows)})
    unchanged = file_hash(DB) == before
    # comparison: the same statements on a plain read-write connection, on a copy of the database
    naive = []
    tmp = tempfile.mkdtemp()
    try:
        for kind, sql in ATTACKS:
            if kind == "slow":
                naive.append({"kind": kind, "sql": sql, "ran": None})
                continue
            copy = os.path.join(tmp, "copy.db")
            shutil.copy(DB, copy)
            con = sqlite3.connect(copy)
            try:
                con.execute(sql).fetchall()
                con.commit()
                naive.append({"kind": kind, "sql": sql, "ran": True})
            except sqlite3.Error:
                naive.append({"kind": kind, "sql": sql, "ran": False})
            finally:
                con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    kinds = sorted({k for k, _ in ATTACKS})
    s = {"attacks": len(ATTACKS), "database_file_unchanged_after_attacks": unchanged,
         "guarded_stopped": pct(sum(a["stopped"] for a in guarded), len(guarded)),
         "guarded_stopped_by_layer": {l: sum(a["stopped_by"] == l for a in guarded) for l in sorted({a["stopped_by"] for a in guarded})},
         "plain_connection_ran_without_error": pct(sum(bool(a["ran"]) for a in naive if a["ran"] is not None), sum(a["ran"] is not None for a in naive)),
         "plain_connection_note": "the 'slow' attacks were not run on the plain connection because they would not finish",
         "by_kind": {k: {"guarded_stopped": pct(sum(a["stopped"] for a in guarded if a["kind"] == k), sum(a["kind"] == k for a in guarded)),
                         "plain_ran": pct(sum(bool(a["ran"]) for a in naive if a["kind"] == k and a["ran"] is not None),
                                          sum(a["kind"] == k and a["ran"] is not None for a in naive)) if k != "slow" else "not run"}
                     for k in kinds}}
    return s, guarded


def run_injections():
    rows = [{"kind": k, "question": q, "blocked": not guardrails.check_input(q).allowed} for k, q in INJECTIONS]
    return {"injections": len(rows),
            **{f"blocked_{k}": pct(sum(r["blocked"] for r in rows if r["kind"] == k), sum(r["kind"] == k for r in rows))
               for k in ("pattern", "paraphrase")},
            "not_blocked": [r["question"] for r in rows if not r["blocked"]]}, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="oracle", choices=["oracle", "gemini"])
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results.json"))
    a = ap.parse_args()
    llm = GeminiLLM(schema_text(DB), retries=5, base_wait=10) if a.backend == "gemini" else OracleLLM({q: s for _, q, s in QUESTIONS})
    qres = run_questions(Agent(DB, llm))
    attacks, attack_rows = run_attacks()
    inj, inj_rows = run_injections()
    report = {"backend": llm.name, "last_model_error": getattr(llm, "last_error", ""), "questions": summarize_questions(qres), "attacks": attacks, "injections": inj}
    json.dump({"report": report, "question_details": qres, "attack_details": attack_rows, "injection_details": inj_rows},
              open(a.out, "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
