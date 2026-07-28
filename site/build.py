#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble site/dist/ for drvs.evemisslab.com.

The site's demo runs the REAL package, so this script copies the actual
core/, client/, and ui/ files rather than keeping site-local copies that would
silently drift out of sync with the thing they're advertising. Same for the
demo corpus: the index, dictionary, and vectors served here are the exact
artifacts examples/demo-corpus builds and commits.

The document rows in the page are rendered from that index at build time, so
the corpus is present in the served HTML — no JavaScript required to read it.
That is the package's central promise ("the reveal layer is additive"), and
generating the rows this way is what makes the demo an honest demonstration of
it rather than an assertion about it.

Usage:  python3 site/build.py
"""
import json
import shutil
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "src"
DIST = ROOT / "site" / "dist"
DEMO = ROOT / "examples" / "demo-corpus"

# Package files the demo actually executes. The ui/ -> core/ and ui/ ->
# client/ relative imports mean this layout has to be preserved exactly.
PACKAGE_FILES = [
    ("core/scoring.js", "drvs/core/scoring.js"),
    ("core/index.js", "drvs/core/index.js"),
    ("client/vector-client.js", "drvs/client/vector-client.js"),
    ("ui/reveal.js", "drvs/ui/reveal.js"),
    ("ui/reveal.css", "drvs/ui/reveal.css"),
    ("ui/search-worker.js", "drvs/ui/search-worker.js"),
]

# Prebuilt demo-corpus artifacts. chunks.* are optional: the demo corpus is
# small enough that chunk vectors may not have been built, and the package is
# specified to degrade rather than fail when they're absent.
DEMO_DATA = [
    ("dist/index.json", "drvs/data/index.json", True),
    ("dist/dictionary.json", "drvs/data/dictionary.json", True),
    ("dist/vectors.bin", "drvs/data/vectors.bin", True),
    ("dist/vectors-meta.json", "drvs/data/vectors-meta.json", True),
    ("dist/chunks.bin", "drvs/data/chunks.bin", False),
    ("dist/chunks-meta.json", "drvs/data/chunks-meta.json", False),
]

# English reason labels for the demo. Shipped as config rather than code to
# exercise the JSON path that a real deployment uses — including the "{}"
# templates, which exist precisely because JSON can't carry functions.
DEMO_CONFIG_LABELS = {
    "exact_title": "exact title match",
    "exact_heading": "section heading match",
    "exact_summary": "exact summary match",
    "exact_keyword": "exact keyword match",
    "lexical": "lexical similarity",
    "trigram": "character-level similarity",
    "alias": "matched known alias “{}”",
    "related_term": "close to related term “{}”",
    "semantic_doc": "summary is semantically close",
    "semantic_chunk": "this passage is close ({})",
    "semantic_chunk_generic": "a passage is semantically close",
}

DEMO_RELATION_TYPES = {
    "s": {"label": "same series as a confirmed match", "relation": "same_series", "weightKey": "series_relation"},
    "p": {"label": "later version of a confirmed match", "relation": "next_version_of", "weightKey": "direct_link_relation"},
    "n": {"label": "earlier version of a confirmed match", "relation": "previous_version_of", "weightKey": "direct_link_relation"},
    "e": {"label": "explicitly cited by a confirmed match", "relation": "explicit_link", "weightKey": "direct_link_relation"},
    "k": {"label": "shares a primary keyword with a confirmed match", "relation": "same_primary_keyword", "weightKey": "series_relation"},
}


def copy(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def render_rows(index: dict) -> str:
    """One <li> per document, straight from the built index.

    Every document lands in the HTML. The reveal layer only ever adds classes
    and data attributes to these rows — it never creates or removes one — so
    what a visitor sees with JavaScript off is the complete corpus.
    """
    rows = []
    for doc in index.get("documents", []):
        doc_id = doc.get("i", "")
        title = doc.get("t", "")
        summary = doc.get("s", "")
        url = doc.get("u") or "#"
        rows.append(
            '      <li>\n'
            f'        <a class="doc" data-doc-id="{escape(doc_id, quote=True)}" href="{escape(url, quote=True)}">\n'
            f'          <span class="doc-id">{escape(doc_id)}</span>\n'
            '          <span class="doc-body">\n'
            f'            <span class="doc-title">{escape(title)}</span>\n'
            f'            <span class="doc-sum">{escape(summary)}</span>\n'
            '          </span>\n'
            '        </a>\n'
            '      </li>'
        )
    return "\n".join(rows)


def build_demo_config() -> dict:
    """The package default config, plus the demo's English labels/relations."""
    base = json.loads((ROOT / "config" / "search.config.json").read_text(encoding="utf-8"))
    base["labels"] = DEMO_CONFIG_LABELS
    base["relationTypes"] = DEMO_RELATION_TYPES
    base["note"] = ("Demo config for drvs.evemisslab.com — the package default plus English "
                    "reason labels, supplied as JSON to exercise the same config path a real "
                    "deployment uses.")
    return base


def main():
    index_path = DEMO / "dist" / "index.json"
    if not index_path.exists():
        sys.exit(f"[drvs-site] missing {index_path} — run examples/demo-corpus/build_demo.py first")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # 1. static source
    for path in SRC.rglob("*"):
        if path.is_file() and path.name != "index.html":
            copy(path, DIST / path.relative_to(SRC))

    # 2. the real package
    missing = [rel for rel, _ in PACKAGE_FILES if not (ROOT / rel).exists()]
    if missing:
        sys.exit(f"[drvs-site] package files missing: {missing}")
    for rel, dest in PACKAGE_FILES:
        copy(ROOT / rel, DIST / dest)

    # 3. demo corpus artifacts
    copied_data, skipped_data = [], []
    for rel, dest, required in DEMO_DATA:
        src = DEMO / rel
        if src.exists():
            copy(src, DIST / dest)
            copied_data.append(dest)
        elif required:
            sys.exit(f"[drvs-site] required demo artifact missing: {src}")
        else:
            skipped_data.append(dest)

    # 4. demo config
    cfg_path = DIST / "drvs" / "data" / "search.config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(build_demo_config(), ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. the page, with rows rendered from the real index
    index = json.loads(index_path.read_text(encoding="utf-8"))
    html = (SRC / "index.html").read_text(encoding="utf-8")
    if "<!--DOCUMENT_ROWS-->" not in html:
        sys.exit("[drvs-site] index.html has no <!--DOCUMENT_ROWS--> placeholder")
    html = html.replace("<!--DOCUMENT_ROWS-->", render_rows(index))
    (DIST / "index.html").write_text(html, encoding="utf-8")

    print(f"[drvs-site] built -> {DIST}")
    print(f"[drvs-site] {len(index.get('documents', []))} documents rendered into the HTML")
    if skipped_data:
        print(f"[drvs-site] optional artifacts not present (channel degrades, as designed): {skipped_data}")


if __name__ == "__main__":
    main()
