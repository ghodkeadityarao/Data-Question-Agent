"""Guardrails: input checks, scope check, output checks and limits. Pure functions, easy to test."""
import re
from dataclasses import dataclass

MAX_QUESTION_CHARS = 500
MAX_STEPS = 4
MAX_ANSWER_CHARS = 2500
MIN_CONFIDENCE = 0.5  # retrieval must cover at least this share of the question's weighted terms

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any |the )?(previous|prior|above|your) (instructions|prompts|rules)",
    r"(reveal|show|print|repeat|output|leak) (me )?(your |the )?(system|hidden|initial) (prompt|instructions|message)",
    r"(reveal|show|print|leak|give me|tell me) (me )?(your |the )?(api[ _-]?key|secret|password|token|credentials)",
    r"\b(what('s| is)|what are) your (api[ _-]?key|secret|password|token|credentials)",
    r"\b(disregard|ignore|forget) .{0,30}\b(rules|instructions|prompt)s?\b",
    r"you are now (a |an |in )?",
    r"\b(jailbreak|DAN mode|developer mode)\b",
    r"pretend (to be|you are|that you)",
    r"act as (if you (are|were)|a |an )",
    r"(override|bypass|disable) (your |the )?(safety|guardrails?|rules|restrictions|filters?)",
    r"</?(system|assistant)>",
]
_INJECTION = [re.compile(p, re.I) for p in INJECTION_PATTERNS]
SECRET_PATTERNS = [r"AIza[0-9A-Za-z_\-]{30,}", r"sk-[A-Za-z0-9]{20,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                   r"AKIA[0-9A-Z]{16}"]
_SECRET = [re.compile(p) for p in SECRET_PATTERNS]


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    category: str = ""  # empty, too_long, injection, out_of_scope, ungrounded, secret, limit


def check_input(question):
    q = (question or "").strip()
    if not q:
        return Decision(False, "The question is empty.", "empty")
    if len(q) > MAX_QUESTION_CHARS:
        return Decision(False, f"The question is longer than {MAX_QUESTION_CHARS} characters.", "too_long")
    if any(ord(c) < 32 and c not in "\n\t" for c in q):
        return Decision(False, "The question contains control characters.", "injection")
    for rx in _INJECTION:
        if rx.search(q):
            return Decision(False, "The request looks like an attempt to change my instructions.", "injection")
    return Decision(True)


def check_scope(hits):
    if not hits or hits[0]["confidence"] < MIN_CONFIDENCE:
        return Decision(False, "I only answer questions that the Kubernetes documentation I have covers, "
                               "and I could not find a match for this one.", "out_of_scope")
    return Decision(True)


def check_output(answer, citations, retrieved_ids):
    if any(rx.search(answer) for rx in _SECRET):
        return Decision(False, "The answer was withheld because it contained something that looks like a secret.", "secret")
    if not citations or any(c not in retrieved_ids for c in citations):
        return Decision(False, "The answer was withheld because it was not grounded in retrieved documentation.", "ungrounded")
    return Decision(True)
