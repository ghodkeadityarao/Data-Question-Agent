"""Guardrails on the question and on the answer. SQL safety lives in sqlguard.py. Pure functions, easy to test."""
import re
from dataclasses import dataclass

MAX_QUESTION_CHARS = 500
MAX_STEPS = 4
MAX_ANSWER_CHARS = 1500

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
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    category: str = ""  # empty, too_long, injection, ungrounded, secret, private_data, limit, declined, model_error


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


def check_output(answer, queries_ok):
    """queries_ok: how many queries ran successfully in this request. An answer needs at least one."""
    if any(rx.search(answer) for rx in _SECRET):
        return Decision(False, "The answer was withheld because it contained something that looks like a secret.", "secret")
    if _EMAIL.search(answer):
        return Decision(False, "The answer was withheld because it contained an email address.", "private_data")
    if not queries_ok:
        return Decision(False, "The answer was withheld because no query had run to back it up.", "ungrounded")
    return Decision(True)
