"""BM25 retrieval over documentation sections, with a query-coverage confidence score.

Pure Python, no embeddings or API keys, so the service and its tests run offline.
"""
import json
import math
import re
from collections import Counter

STOPWORDS = set("""a an and are as at be by can do does for from how i in into is it its of on or that the their
there this to was what when where which who why will with you your about should would could if my we our not no
use used using get set make""".split())
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)  # split camelCase: ReadWriteOnce -> read write once
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def stem(tok):
    for suf in ("ing", "es", "s", "ed"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def terms(text):
    return [stem(t) for t in tokenize(text)]


class Retriever:
    def __init__(self, sections, k1=1.5, b=0.75):
        self.sections, self.k1, self.b = sections, k1, b
        self.by_id = {s["id"]: s for s in sections}
        self.docs = []
        for s in sections:
            # title and heading carry extra weight: repeat them
            toks = terms(f"{s['title']} {s['heading']} {s['title']} {s['heading']} {s['text']}")
            self.docs.append(Counter(toks))
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        n = len(self.docs)
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.unseen_idf = math.log(1 + n)  # an unseen query term counts as unmatched, with the highest weight

    @classmethod
    def from_file(cls, path):
        return cls(json.load(open(path, encoding="utf-8")))

    def _score(self, q_terms, i):
        d, dl, score = self.docs[i], self.lens[i], 0.0
        for t in set(q_terms):
            f = d.get(t, 0)
            if f:
                score += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
        return score

    def confidence(self, q_terms, i):
        """Share of the query's idf weight whose terms appear in section i, from 0 to 1."""
        qs = set(q_terms)
        if not qs:
            return 0.0
        total = sum(self.idf.get(t, self.unseen_idf) for t in qs)
        hit = sum(self.idf[t] for t in qs if self.docs[i].get(t))
        return hit / total if total else 0.0

    def search(self, query, k=3):
        q = terms(query)
        if not q:
            return []
        scored = sorted(((self._score(q, i), i) for i in range(len(self.docs))), reverse=True)[:k]
        out = []
        for sc, i in scored:
            if sc <= 0:
                continue
            s = self.sections[i]
            out.append({**s, "score": round(sc, 3), "confidence": round(self.confidence(q, i), 3)})
        return out
