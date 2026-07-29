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
import re
import shutil
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "src"
DIST = ROOT / "site" / "dist"
DEMO = ROOT / "examples" / "demo-corpus"

sys.path.insert(0, str(ROOT))
from build.extract import parse_frontmatter  # noqa: E402  (the package's own parser)

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


# The demo corpus uses only `## ` headings and blank-line-separated
# paragraphs — verified across all nine files — so a full markdown library
# would be a dependency bought for nothing. If the corpus ever grows richer
# syntax, replace this rather than extending it.
def render_markdown(body: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^##\s+(.+)$", block)
        if m:
            out.append(f"        <h2>{escape(m.group(1).strip())}</h2>")
        else:
            text = escape(" ".join(line.strip() for line in block.split("\n")))
            out.append(f"        <p>{text}</p>")
    return "\n".join(out)


def field_rows(doc: dict, fm: dict) -> str:
    """The index record, rendered with provenance.

    `title` and `keywords` here are author_declared because this corpus's
    frontmatter declares them. `summary` and `headings` are read out of the
    document body by the extractor — headings verbatim, the summary as the
    first real paragraph — so they are labeled as derived. That distinction is
    the entire reason the metadata carries source tags at all.
    """
    declared_kw = isinstance(fm.get("keywords"), list) and fm.get("keywords")

    def row(label, value, source):
        cls = "src-author" if source == "author_declared" else "src-system"
        nice = "author declared" if source == "author_declared" else "system inferred"
        return (
            '        <div class="field">\n'
            f'          <dt>{escape(label)}<span class="src {cls}">{nice}</span></dt>\n'
            f'          <dd>{value}</dd>\n'
            '        </div>'
        )

    def chips(items):
        return " ".join(f'<code class="chip-val">{escape(i)}</code>' for i in items) or "<em>none</em>"

    rows = [
        row("id", f'<code class="chip-val">{escape(doc["i"])}</code>', "author_declared"),
        row("title", escape(doc.get("t", "")), "author_declared"),
        row("summary", escape(doc.get("s", "")), "system_inferred"),
        row("headings", chips(doc.get("h") or []), "author_declared"),
        row("keywords", chips(doc.get("k") or []),
            "author_declared" if declared_kw else "system_inferred"),
    ]
    if doc.get("d"):
        rows.append(row("date", escape(doc["d"]), "author_declared"))
    return "\n".join(rows)


def related_block(doc: dict, by_id: dict) -> str:
    """Curated links only — never similarity, never a guess.

    This is exactly the data the relation channel reads. Seeing it on the page
    is what makes a Tier D "same series as a confirmed match" result checkable
    rather than something the reader has to take on faith.
    """
    rel = doc.get("r") or []
    if not rel:
        return ""
    items = []
    for entry in rel:
        target_id = entry[0] if isinstance(entry, (list, tuple)) else entry
        target = by_id.get(target_id)
        if not target:
            continue
        items.append(
            '        <li><a class="rel" href="{url}">'
            '<span class="rel-id">{tid}</span>'
            '<span class="rel-title">{title}</span></a></li>'.format(
                url=escape(target.get("u") or "#", quote=True),
                tid=escape(target_id),
                title=escape(target.get("t", "")),
            )
        )
    if not items:
        return ""
    return (
        '    <aside class="related">\n'
        '      <h2>Curated relations</h2>\n'
        '      <p class="related-intro">Declared in the index, not inferred from similarity. '
        'A query matching one of these can surface this document through the relation '
        'channel — labeled as a relation, never disguised as a direct hit.</p>\n'
        '      <ul>\n' + "\n".join(items) + '\n      </ul>\n'
        '    </aside>'
    )


def build_document_pages(index: dict, template: str) -> int:
    by_id = {d["i"]: d for d in index.get("documents", [])}
    written = 0

    for doc in index.get("documents", []):
        doc_id = doc["i"]
        md_path = DEMO / "docs" / f"{doc_id}.md"
        if not md_path.exists():
            sys.exit(f"[drvs-site] no source markdown for indexed document '{doc_id}' ({md_path})")

        fm, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))

        meta_bits = []
        if doc.get("d"):
            meta_bits.append(escape(doc["d"]))
        if fm.get("language"):
            meta_bits.append(escape(fm["language"]))
        meta_bits.append("DRVS demo corpus")

        page = (
            template
            .replace("{{TITLE}}", escape(doc.get("t", "")))
            .replace("{{DOC_ID}}", escape(doc_id))
            .replace("{{SUMMARY_ATTR}}", escape(doc.get("s", ""), quote=True))
            .replace("{{META}}", " · ".join(meta_bits))
            .replace("{{BODY}}", render_markdown(body))
            .replace("{{EXTRACTED}}", field_rows(doc, fm))
            .replace("{{RELATED}}", related_block(doc, by_id))
        )

        # The index's own `u` is the source of truth for where this lives, so
        # a link in the corpus list can never point somewhere unbuilt.
        url = doc.get("u") or f"/demo/{doc_id}/"
        out_path = DIST / url.strip("/") / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page, encoding="utf-8")
        written += 1

    return written


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

    # 1. static source (files starting with "_" are templates, not assets)
    for path in SRC.rglob("*"):
        if path.is_file() and path.name != "index.html" and not path.name.startswith("_"):
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

    # 6. a real page per document — the corpus list links to these, and a demo
    #    whose links go nowhere is a worse advertisement than no demo at all
    doc_template = (SRC / "_document.html").read_text(encoding="utf-8")
    written = build_document_pages(index, doc_template)

    print(f"[drvs-site] built -> {DIST}")
    print(f"[drvs-site] {len(index.get('documents', []))} documents rendered into the HTML")
    print(f"[drvs-site] {written} document pages written")
    if skipped_data:
        print(f"[drvs-site] optional artifacts not present (channel degrades, as designed): {skipped_data}")


if __name__ == "__main__":
    main()
