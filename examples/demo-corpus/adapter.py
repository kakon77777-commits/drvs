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


# The corpus exists in two languages. They are deliberately PARALLEL — same
# ids, same relations, same structure — so the demo behaves identically in
# both and any difference you see between them is a real difference in how
# the engine handles the two scripts, not an artefact of different content.
#
# That parallelism is also the point: it is direct evidence the package is not
# quietly CJK-only (or quietly Latin-only). The lexical channel in particular
# takes very different paths for the two — character bigrams for Chinese,
# whitespace tokens for English — and running the same nine documents through
# both is how you check that claim rather than assert it.
LOCALES = {
    "zh-Hant": {
        "docs_dir": "docs",
        # Hand-curated series relations — real editorial groupings (these two
        # pairs of demo docs really are companion pieces), not guessed.
        "series": {
            "陽台番茄系列": ["balcony-tomato-basics", "balcony-tomato-pests"],
            "手沖咖啡系列": ["pourover-temp-grind", "coffee-bean-storage"],
        },
        # A real regional naming variant (Taiwan 酪梨 vs. Mainland 牛油果 for
        # avocado), not an invented alias — matching this package's
        # zero-guess-aliasing stance (see schema/document.md §4).
        "dictionary": [
            {
                "concept_id": "concept-avocado",
                "canonical": "酪梨",
                "aliases": [{"term": "牛油果", "weight": 0.95}],
                "related": [],
            },
        ],
        # Chinese embedding model; the query instruction is the one BGE's own
        # model card specifies for retrieval.
        "model": "BAAI/bge-small-zh-v1.5",
        "onnx_model": "Xenova/bge-small-zh-v1.5",
        # Embedding width is a property of the MODEL, not of the package —
        # bge-small-zh is 512-wide while its English sibling is 384. Getting
        # this wrong fails loudly at pack time, which is the good outcome; the
        # bad one would be a mismatched reader silently misinterpreting the
        # byte stream, so the width is written into the meta file the client
        # reads rather than assumed on either side.
        "dim": 512,
        "query_instruction": "为这个句子生成表示以用于检索相关文章：",
    },
    "en": {
        "docs_dir": "docs-en",
        "series": {
            "Balcony tomato series": ["balcony-tomato-basics", "balcony-tomato-pests"],
            "Pour-over coffee series": ["pourover-temp-grind", "coffee-bean-storage"],
        },
        # The English counterpart is chosen to have the same SHAPE as the
        # Chinese one — a genuine regional/historical name for the same fruit,
        # attested rather than invented. "Alligator pear" was the ordinary
        # American name for the avocado well into the twentieth century.
        "dictionary": [
            {
                "concept_id": "concept-avocado",
                "canonical": "avocado",
                "aliases": [{"term": "alligator pear", "weight": 0.95}],
                "related": [],
            },
        ],
        "model": "BAAI/bge-small-en-v1.5",
        "onnx_model": "Xenova/bge-small-en-v1.5",
        "dim": 384,
        "query_instruction": "Represent this sentence for searching relevant passages: ",
    },
}

DEFAULT_LOCALE = "zh-Hant"


def locale_config(locale: str = DEFAULT_LOCALE) -> dict:
    if locale not in LOCALES:
        raise ValueError(f"unknown locale {locale!r}; known: {sorted(LOCALES)}")
    return LOCALES[locale]


def _build_series_relations(series: dict):
    related_map = {}
    series_label = {}
    for label, ids in series.items():
        for did in ids:
            related_map.setdefault(did, set()).update(x for x in ids if x != did)
            series_label[did] = label
    return related_map, series_label


def load_documents(locale: str = DEFAULT_LOCALE) -> list:
    cfg = locale_config(locale)
    docs_dir = DEMO_DIR / cfg["docs_dir"]

    # Routed through merge_relations() even though this demo has only one
    # relation source, to demonstrate the real call shape a multi-source
    # adapter would use (see build/relations.py for why the merge step matters
    # once two sources can disagree about the same pair).
    related_map, series_label = _build_series_relations(cfg["series"])
    relation_source = {k: [(t, "s") for t in v] for k, v in related_map.items()}
    merged = merge_relations(relation_source, priority=["s"])

    docs = []
    for path in sorted(docs_dir.glob("*.md")):
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
    out = {}
    for loc in LOCALES:
        docs = load_documents(loc)
        out[loc] = {"count": len(docs), "ids": [d["id"] for d in docs]}
    # The two locales must stay id-for-id parallel; if they drift, every
    # cross-locale comparison the demo invites becomes misleading.
    id_sets = {loc: set(v["ids"]) for loc, v in out.items()}
    out["parallel"] = len({frozenset(s) for s in id_sets.values()}) == 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
