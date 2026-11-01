"""Build the docs corpus: sections of Kubernetes documentation pages (article body only).

    python data/build_corpus.py --max-pages 150

Polite by design: one request at a time, a short pause between requests, a page cap.
Writes data/sections.json. Each section: id, url, title, heading, text.
"""
import argparse
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://kubernetes.io"
INDEXES = ["/docs/concepts/workloads/", "/docs/concepts/services-networking/", "/docs/concepts/storage/",
           "/docs/concepts/configuration/", "/docs/concepts/security/", "/docs/concepts/cluster-administration/",
           "/docs/concepts/scheduling-eviction/", "/docs/tasks/configure-pod-container/",
           "/docs/tasks/run-application/", "/docs/tasks/administer-cluster/"]
SKIP = {"feedback", "what's next", "whats next", "see also", "before you begin"}
UA = {"User-Agent": "k8s-agent-service-corpus-builder (personal project, polite crawl)"}


def clean(t):
    return re.sub(r"\s+", " ", t).strip()


def page_links(index):
    html = requests.get(BASE + index, headers=UA, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    out = set()
    for a in soup.find_all("a", href=True):
        h = a["href"].split("#")[0]
        if h.startswith(index) and h != index and h.count("/") >= index.count("/"):
            out.add(urljoin(BASE, h))
    return sorted(out)


def sections(url):
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code != 200:
        return []
    c = BeautifulSoup(r.text, "html.parser").select_one("div.td-content")
    if c is None or c.find("h1") is None:
        return []
    title, out = clean(c.find("h1").get_text()), []
    for h in c.find_all("h2"):
        heading = clean(h.get_text())
        if heading.lower() in SKIP:
            continue
        parts, n = [], h.find_next_sibling()
        while n is not None and n.name != "h2":
            if n.name in ("p", "ul", "ol"):
                parts.append(clean(n.get_text(" ")))
            n = n.find_next_sibling()
        text = " ".join(parts)
        if len(text.split()) >= 40:
            out.append({"url": url, "title": title, "heading": heading, "text": text})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "sections.json"))
    a = ap.parse_args()
    urls = []
    for idx in INDEXES:
        urls += page_links(idx)
        time.sleep(0.3)
    urls = sorted(set(urls))[: a.max_pages * 3]
    # spread the cap across indexes instead of taking the first alphabetical pages
    step = max(1, len(urls) // a.max_pages)
    urls = urls[::step][: a.max_pages]
    secs = []
    for i, u in enumerate(urls):
        try:
            secs += sections(u)
        except Exception as e:
            print("skip", u, e)
        time.sleep(0.3)
        if i % 25 == 0:
            print(f"{i}/{len(urls)} pages, {len(secs)} sections", flush=True)
    for i, s in enumerate(secs):
        s["id"] = f"s{i:04d}"
    json.dump(secs, open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"wrote {len(secs)} sections from {len({s['url'] for s in secs})} pages to {a.out}")


if __name__ == "__main__":
    main()
