#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS demo-corpus adapter.

Turns the markdown files in docs/ into a list of DRVS Document records (see
schema/document.md), plus a small hand-curated relations map and a tiny
dictionary — demonstrating the adapter contract a real corpus integration
must fulfill: DRVS itself has no idea what a "markdown file with YAML
frontmatter" is, that knowledge lives entirely in adapters like this one.

This is intentionally the SIMPLEST possible adapter — no HTML/Word-export
cruft stripping, no heuristic keyword extraction, no citation-graph parsing.
A real corpus adapter will likely need more robustness than this file has;
what this demonstrates is the SHAPE of the contract, not a production-grade
markdown parser. See docs/ADAPTING_A_CORPUS.md for what a real adapter
typically needs beyond this.
"""
import re
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).parent
DOCS_DIR = DEMO_DIR / "docs"
REPO_ROOT = DEMO_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from build.relations import merge_relations  # noqa: E402

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
FM_KV_RE = re.compile(r'^([A-Za-z_]+):\s*(.*)$')
FM_LIST_ITEM_RE = re.compile(r'^\s*-\s+(.*)$')
HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter(text: str):
    """Tiny hand-rolled subset of YAML: `key: value` and `key:\\n  - item`
    lists. Good enough for this demo's frontmatter; not a general parser —
    use a real YAML library in a production adapter."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    fm: dict = {}
    lines = m.group(1).split("\n")
    i = 0
    while i < len(lines):
        km = FM_KV_RE.match(lines[i])
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val == "":
                items, j = [], i + 1
                while j < len(lines):
                    im = FM_LIST_ITEM_RE.match(lines[j])
                    if not im:
                        break
                    items.append(im.group(1).strip())
                    j += 1
                if items:
                    fm[key] = items
                    i = j
                    continue
            else:
                fm[key] = val
        i += 1
    return fm, body


def _extract_headings(body: str, limit: int = 6) -> list:
    heads = []
    for hm in HEADING_RE.finditer(body):
        h = hm.group(1).strip()
        if h and h not in heads:
            heads.append(h)
        if len(heads) >= limit:
            break
    return heads


def _extract_summary(body: str, limit: int = 120) -> str:
    for para in body.split("\n\n"):
        s = para.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"\s+", " ", s)
        return s[:limit] + ("…" if len(s) > limit else "")
    return ""


# Hand-curated series relations — real editorial groupings (these two pairs
# of demo docs really are companion pieces), not guessed. Routed through
# merge_relations() even though this demo only has one relation source, to
# demonstrate the real call shape a multi-source adapter would use (see
# build/relations.py for why the merge step matters once you have more than
# one source that can disagree about the same pair).
SERIES = {
    "陽台番茄系列": ["balcony-tomato-basics", "balcony-tomato-pests"],
    "手沖咖啡系列": ["pourover-temp-grind", "coffee-bean-storage"],
}

# A tiny, honest dictionary entry: a real regional naming variant (Taiwan
# "酪梨" vs. Mainland "牛油果" for avocado), not an invented alias — matching
# this package's zero-guess-aliasing stance (see schema/document.md §4).
DICTIONARY = [
    {
        "concept_id": "concept-avocado",
        "canonical": "酪梨",
        "aliases": [{"term": "牛油果", "weight": 0.95}],
        "related": [],
    },
]


def _build_series_relations():
    related_map = {}
    series_label = {}
    for label, ids in SERIES.items():
        for did in ids:
            related_map.setdefault(did, set()).update(x for x in ids if x != did)
            series_label[did] = label
    return related_map, series_label


def load_documents() -> list:
    related_map, series_label = _build_series_relations()
    relation_source = {k: [(t, "s") for t in v] for k, v in related_map.items()}
    merged = merge_relations(relation_source, priority=["s"])

    docs = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        doc_id = path.stem
        raw = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        docs.append({
            "id": doc_id,
            "title": fm.get("title", doc_id),
            "url": f"/demo/{doc_id}/",
            "date": fm.get("date", ""),
            "language": fm.get("language", ""),
            "summary": _extract_summary(body),
            "headings": _extract_headings(body),
            "body_text": body,
            "keywords": fm.get("keywords", []),
            "series": [series_label[doc_id]] if doc_id in series_label else [],
            "related_ids": [{"id": t, "type": ty} for t, ty in merged.get(doc_id, [])],
        })
    return docs


if __name__ == "__main__":
    import json
    docs = load_documents()
    print(json.dumps(
        {"count": len(docs), "ids": [d["id"] for d in docs]},
        ensure_ascii=False, indent=2,
    ))
