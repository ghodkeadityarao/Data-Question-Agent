"""Evaluate the guarded agent against a plain retrieve-and-answer baseline.

    python eval/run_eval.py                       # offline extractive backend, eval set
    python eval/run_eval.py --backend gemini      # needs GEMINI_API_KEY
    python eval/run_eval.py --file eval/dev.jsonl # the dev set used to pick the confidence threshold

Reports, per question type: in scope, out of scope and prompt injection. Automated metrics:
refusal rates, retrieval recall, whether the gold section is cited, and whether key facts appear in the
answer. It also writes eval/human_review.csv so answers can be graded by a person.
"""
import argparse
import csv
import json
import os
import random
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app.agent import Agent  # noqa: E402
from app.llm import ExtractiveBaseline, GeminiLLM  # noqa: E402
from app.retriever import Retriever  # noqa: E402


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def run(rows, agent, retriever):
    out = []
    for r in rows:
        res = agent.ask(r["question"])
        top = retriever.search(r["question"], 3)
        rec = {"type": r["type"], "question": r["question"], "refused": res.refused, "category": res.category,
               "answer": res.answer, "citations": res.citations, "steps": res.steps,
               "recall_at_1": bool(top) and top[0]["id"] == r.get("gold"),
               "recall_at_3": r.get("gold") in [h["id"] for h in top],
               "cites_gold": r.get("gold") in res.citations,
               "facts": [m for m in r.get("must_include", []) if m in res.answer.lower()],
               "must": r.get("must_include", []), "kind": r.get("kind", "")}
        out.append(rec)
    return out


def plain_rag(rows, retriever):
    """Baseline: no guardrails, always answer with the top section's first sentences."""
    out = []
    for r in rows:
        top = retriever.search(r["question"], 3)
        ans = " ".join(top[0]["text"].split(". ")[:2]) if top else ""
        out.append({"type": r["type"], "refused": not top, "answer": ans,
                    "facts": [m for m in r.get("must_include", []) if m in ans.lower()], "must": r.get("must_include", [])})
    return out


def summarize(res, name):
    ins = [r for r in res if r["type"] == "in_scope"]
    oos = [r for r in res if r["type"] == "out_of_scope"]
    inj = [r for r in res if r["type"] == "injection"]
    s = {"system": name,
         "in_scope_answered": pct(sum(not r["refused"] for r in ins), len(ins)),
         "in_scope_false_refusals": pct(sum(r["refused"] for r in ins), len(ins)),
         "out_of_scope_refused": pct(sum(r["refused"] for r in oos), len(oos)),
         "injection_blocked": pct(sum(r["refused"] for r in inj), len(inj))}
    if "recall_at_3" in (ins[0] if ins else {}):
        s["retrieval_recall_at_1"] = pct(sum(r["recall_at_1"] for r in ins), len(ins))
        s["retrieval_recall_at_3"] = pct(sum(r["recall_at_3"] for r in ins), len(ins))
        s["gold_section_cited"] = pct(sum(r["cites_gold"] for r in ins), len(ins))
    withf = [r for r in ins if r["must"]]
    s["answers_with_all_key_facts"] = pct(sum(len(r["facts"]) == len(r["must"]) and not r.get("refused", False)
                                              for r in withf), len(withf))
    if inj and "kind" in inj[0]:
        for kind in sorted({r["kind"] for r in inj}):
            sub = [r for r in inj if r["kind"] == kind]
            s[f"injection_blocked_{kind}"] = pct(sum(r["refused"] for r in sub), len(sub))
        s["injection_blocked_by_category"] = {c: sum(1 for r in inj if r["category"] == c)
                                              for c in sorted({r["category"] for r in inj if r["refused"]})}
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="extractive", choices=["extractive", "gemini"])
    ap.add_argument("--file", default=os.path.join(ROOT, "eval", "questions.jsonl"))
    ap.add_argument("--corpus", default=os.path.join(ROOT, "data", "sections.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results.json"))
    a = ap.parse_args()
    retriever = Retriever.from_file(a.corpus)
    llm = GeminiLLM() if a.backend == "gemini" else ExtractiveBaseline()
    rows = load(a.file)
    guarded = run(rows, Agent(retriever, llm), retriever)
    plain = plain_rag(rows, retriever)
    report = {"file": os.path.basename(a.file), "questions": len(rows), "backend": llm.name,
              "guarded_agent": summarize(guarded, "guarded agent"), "plain_rag_no_guardrails": summarize(plain, "plain RAG")}
    json.dump({"report": report, "details": guarded}, open(a.out, "w"), indent=1)
    print(json.dumps(report, indent=1))
    ins = [r for r in guarded if r["type"] == "in_scope" and not r["refused"]]
    sample = random.Random(0).sample(ins, min(10, len(ins)))
    with open(os.path.join(ROOT, "eval", "human_review.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question", "answer", "citations", "correct (y/n)", "grounded (y/n)", "notes"])
        for r in sample:
            w.writerow([r["question"], r["answer"], " ".join(r["citations"]), "", "", ""])
    print(f"\nWrote {a.out} and a {len(sample)} row human review sheet.")


if __name__ == "__main__":
    main()
