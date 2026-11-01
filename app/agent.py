"""Guarded retrieval agent: input checks, scope check, a bounded search loop, output checks."""
from collections import OrderedDict
from dataclasses import dataclass, field

from . import guardrails as g


@dataclass
class Result:
    answer: str
    citations: list = field(default_factory=list)
    refused: bool = False
    reason: str = ""
    category: str = ""
    steps: int = 0
    sources: list = field(default_factory=list)


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
    def __init__(self, retriever, llm, max_steps=g.MAX_STEPS, k=3):
        self.retriever, self.llm, self.max_steps, self.k = retriever, llm, max_steps, k
        self.sessions = SessionStore()

    def _refuse(self, d, steps=0):
        return Result(answer=d.reason, refused=True, reason=d.reason, category=d.category, steps=steps)

    def ask(self, question, session_id=None):
        d = g.check_input(question)
        if not d.allowed:
            return self._refuse(d)
        question = question.strip()
        first = self.retriever.search(question, self.k)
        d = g.check_scope(first)
        if not d.allowed:
            return self._refuse(d)
        observations, seen = [first], {h["id"]: h for h in first}
        history = self.sessions.history(session_id) if session_id else []
        for step in range(1, self.max_steps + 1):
            move = self.llm.next_step(question, history, observations)
            if move.get("action") == "search":
                hits = self.retriever.search(str(move.get("query", question))[:200], self.k)
                observations.append(hits)
                seen.update({h["id"]: h for h in hits})
                continue
            if move.get("action") != "answer":
                return self._refuse(g.Decision(False, "The language model backend failed to produce a valid answer.",
                                               "model_error"), step)
            text = str(move.get("text", ""))[: g.MAX_ANSWER_CHARS]
            cites = [c for c in move.get("citations", []) if isinstance(c, str)]
            d = g.check_output(text, cites, set(seen))
            if not d.allowed:
                return self._refuse(d, step)
            if session_id:
                self.sessions.add(session_id, question, text)
            return Result(answer=text, citations=cites, steps=step,
                          sources=[{"id": c, "url": seen[c]["url"], "title": seen[c]["title"],
                                    "heading": seen[c]["heading"]} for c in cites])
        return self._refuse(g.Decision(False, f"Stopped after {self.max_steps} steps without a final answer.", "limit"),
                            self.max_steps)
