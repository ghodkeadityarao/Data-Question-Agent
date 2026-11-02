"""Guarded data agent: input checks, a bounded write-run-fix SQL loop, output checks."""
from collections import OrderedDict
from dataclasses import dataclass, field

from . import guardrails as g
from .sqlguard import run_query


@dataclass
class Result:
    answer: str
    refused: bool = False
    reason: str = ""
    category: str = ""
    steps: int = 0
    sql: str = ""                                   # the last query that ran successfully
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    truncated: bool = False
    queries_run: int = 0
    queries_blocked: int = 0                        # attempts stopped by the SQL guardrails


class SessionStore:
    """Bounded in-memory chat memory: last few turns per session, oldest sessions evicted."""

    def __init__(self, max_sessions=1000, max_turns=6):
        self.max_sessions, self.max_turns, self.data = max_sessions, max_turns, OrderedDict()

    def history(self, sid):
        return list(self.data.get(sid, []))

    def add(self, sid, q, a):
        turns = self.data.setdefault(sid, [])
        turns.append((q, a))
        del turns[: -self.max_turns]
        self.data.move_to_end(sid)
        while len(self.data) > self.max_sessions:
            self.data.popitem(last=False)


class Agent:
    def __init__(self, db_path, llm, max_steps=g.MAX_STEPS):
        self.db_path, self.llm, self.max_steps = db_path, llm, max_steps
        self.sessions = SessionStore()

    def ask(self, question, session_id=None):
        d = g.check_input(question)
        if not d.allowed:
            return Result(d.reason, True, d.reason, d.category)
        question = question.strip()
        history = self.sessions.history(session_id) if session_id else []
        observations, last, ok, blocked = [], None, 0, 0

        def stop(decision, step):
            return Result(decision.reason, True, decision.reason, decision.category, step, queries_run=len(observations),
                          queries_blocked=blocked)

        for step in range(1, self.max_steps + 1):
            move = self.llm.next_step(question, history, observations)
            action = move.get("action")
            if action == "sql":
                res = run_query(self.db_path, str(move.get("query", "")))
                blocked += res.blocked
                observations.append({"query": str(move.get("query", "")), "ok": res.ok, "columns": res.columns,
                                     "rows": res.rows, "truncated": res.truncated, "error": res.error})
                if res.ok:
                    ok, last = ok + 1, (move["query"], res)
                continue
            if action == "refuse":
                reason = str(move.get("reason") or "I can only answer read-only questions about the store data.")[:300]
                return stop(g.Decision(False, reason, "declined"), step)
            if action != "answer":
                return stop(g.Decision(False, "The language model backend failed to produce a valid reply.", "model_error"), step)
            text = str(move.get("text", ""))[: g.MAX_ANSWER_CHARS]
            d = g.check_output(text, ok)
            if not d.allowed:
                return stop(d, step)
            if session_id:
                self.sessions.add(session_id, question, text)
            sql, res = last
            return Result(text, steps=step, sql=sql.strip(), columns=res.columns, rows=res.rows, truncated=res.truncated,
                          queries_run=len(observations), queries_blocked=blocked)
        return stop(g.Decision(False, f"Stopped after {self.max_steps} steps without a final answer.", "limit"), self.max_steps)
