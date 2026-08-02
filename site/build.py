#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble site/dist/ for drvs.evemisslab.com.

Two things this script refuses to do, both for the same reason — a demo that
misrepresents the product is worse than no demo:

  * It never keeps site-local copies of the package. core/, client/, and ui/
    are copied out of the repo at build time, so what the demo executes is
    what the repo ships.

  * It never injects the corpus with JavaScript. Document rows are rendered
    into the HTML here, at build time, because the package's central claim is
    that the reveal layer is *additive* — and a demo of an additive layer that
    needs JS to show its content would be arguing against itself.

The site is bilingual. Each locale gets its own copy of the corpus (the demo
corpus exists in parallel English and Traditional Chinese editions, same ids
and same relations), its own index, its own dictionary, its own embedding
model, and its own document pages. Nothing is shared but the package itself
and the stylesheet — in particular an English page never shows a Chinese
corpus, which is the whole point of building it this way rather than
translating the chrome and leaving the content alone.

Usage:  python3 site/build.py
"""
import json
import re
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "src"
I18N = ROOT / "site" / "i18n"
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

# Per-locale demo artifacts. chunks.* are optional — the package is specified
# to degrade rather than fail when they're absent.
DEMO_DATA = [
    ("index.json", True),
    ("dictionary.json", True),
    ("vectors.bin", True),
    ("vectors-meta.json", True),
    ("chunks.bin", False),
    ("chunks-meta.json", False),
]

# Reason labels per locale, shipped as JSON rather than code so the demo
# exercises the same config path a real deployment uses — including the "{}"
# templates, which exist precisely because JSON can't carry functions.
LABELS = {
    "en": {
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
    },
    "zh-Hant": {
        "exact_title": "標題精確命中",
        "exact_heading": "章節標題命中",
        "exact_summary": "摘要精確命中",
        "exact_keyword": "關鍵詞精確命中",
        "lexical": "詞彙相似命中",
        "trigram": "字元近似命中",
        "alias": "命中已確認別名「{}」",
        "related_term": "與相關詞「{}」近似",
        "semantic_doc": "摘要語義近似",
        "semantic_chunk": "段落語義近似（{}）",
        "semantic_chunk_generic": "段落語義近似",
    },
}

# Relation labels live alongside their scoring weight, so they are NOT part of
# `labels` — translating only `labels` leaves one foreign-language row sitting
# in an otherwise-translated result list. (core/scoring.js exports
# RELATION_TYPES_EN for JS callers; this is the JSON equivalent.)
RELATION_TYPES = {
    "en": {
        "s": {"label": "same series as a confirmed match", "relation": "same_series", "weightKey": "series_relation"},
        "p": {"label": "later version of a confirmed match", "relation": "next_version_of", "weightKey": "direct_link_relation"},
        "n": {"label": "earlier version of a confirmed match", "relation": "previous_version_of", "weightKey": "direct_link_relation"},
        "e": {"label": "explicitly cited by a confirmed match", "relation": "explicit_link", "weightKey": "direct_link_relation"},
        "k": {"label": "shares a primary keyword with a confirmed match", "relation": "same_primary_keyword", "weightKey": "series_relation"},
    },
    "zh-Hant": {
        "s": {"label": "與直接結果屬於同系列", "relation": "same_series", "weightKey": "series_relation"},
        "p": {"label": "是已匹配結果的後續版本", "relation": "next_version_of", "weightKey": "direct_link_relation"},
        "n": {"label": "是已匹配結果的前一版本", "relation": "previous_version_of", "weightKey": "direct_link_relation"},
        "e": {"label": "與直接結果有明確引用關係", "relation": "explicit_link", "weightKey": "direct_link_relation"},
        "k": {"label": "與直接結果共享核心關鍵詞", "relation": "same_primary_keyword", "weightKey": "series_relation"},
    },
}

PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z][\w.]*)\}\}")


def copy(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def lookup(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def fill(template: str, data: dict) -> str:
    """Substitute {{dotted.key}} from the locale bundle.

    Values are inserted RAW, not escaped: the i18n files are ours and several
    strings intentionally carry markup (<em>, <code>, <br>). They are content,
    not user input. Anything derived from the corpus goes through escape()
    at its own call site instead.
    """
    def repl(m):
        val = lookup(data, m.group(1))
        return m.group(0) if val is None else str(val)
    return PLACEHOLDER_RE.sub(repl, template)


def unresolved(text: str) -> list:
    return sorted(set(PLACEHOLDER_RE.findall(text)))


def doc_url(loc: dict, doc_id: str) -> str:
    """Route one document into a locale's URL space.

    The adapter emits "/demo/{id}/" because it has no idea it will be served
    under two locales; routing belongs to the site, so it is applied here
    rather than baked into the example.

    Deliberately derived from the ID, not from the record's current `u`: the
    index's URLs get rewritten in place during the build, and a prefix
    function that reads its own output applies the prefix twice on the second
    call ("/zh/zh/demo/..."). Taking the id makes it idempotent by
    construction rather than by remembering call order.
    """
    return loc["dir"].rstrip("/") + f"/demo/{doc_id}/"


# ---------------------------------------------------------------- fragments

def render_facts(loc: dict) -> str:
    return "\n".join(
        f'      <li><span>{escape(str(f["n"]))}</span>{escape(f["label"])}</li>'
        for f in loc["hero"]["facts"]
    )


def render_chips(loc: dict) -> str:
    return "\n".join(
        '      <button class="chip" data-q="{q}">{q} <i>{hint}</i></button>'.format(
            q=escape(c["q"], quote=True), hint=escape(c["hint"]))
        for c in loc["demo"]["chips"]
    )


def render_wedge(loc: dict) -> str:
    out = []
    for i, s in enumerate(loc["tiers"]["steps"]):
        masked = " masked" if i == len(loc["tiers"]["steps"]) - 1 else ""
        out.append(
            f'      <div class="step{masked}" role="listitem" style="--o:{s["v"]}">\n'
            '        <div class="patch"></div>\n'
            f'        <b>{escape(s["k"])}</b><span class="val">{escape(s["v"])}</span>\n'
            f'        <p>{s["d"]}</p>\n'
            '      </div>'
        )
    return "\n".join(out)


def render_cards(loc: dict) -> str:
    out = []
    for c in loc["channels"]["cards"]:
        cls = "card"
        if c.get("accent"):
            cls += " accent"
        if c.get("quiet"):
            cls += " quiet"
        out.append(
            f'      <article class="{cls}">\n'
            f'        <h3>{c["h"]}</h3>\n'
            f'        <p>{c["p"]}</p>\n'
            f'        <code>{escape(c["c"])}</code>\n'
            '      </article>'
        )
    return "\n".join(out)


def render_rows(index: dict, loc: dict) -> str:
    """One <li> per document, straight from the built index.

    Every document lands in the HTML. The reveal layer only ever adds classes
    and data attributes to these rows — it never creates or removes one — so
    what a visitor sees with JavaScript off is the complete corpus.
    """
    rows = []
    for doc in index.get("documents", []):
        rows.append(
            '      <li>\n'
            f'        <a class="doc" data-doc-id="{escape(doc["i"], quote=True)}" href="{escape(doc_url(loc, doc["i"]), quote=True)}">\n'
            f'          <span class="doc-id">{escape(doc["i"])}</span>\n'
            '          <span class="doc-body">\n'
            f'            <span class="doc-title">{escape(doc.get("t", ""))}</span>\n'
            f'            <span class="doc-sum">{escape(doc.get("s", ""))}</span>\n'
            '          </span>\n'
            '        </a>\n'
            '      </li>'
        )
    return "\n".join(rows)


# ------------------------------------------------------------ document pages

def render_markdown(body: str) -> str:
    """The demo corpus uses only `## ` headings and blank-line-separated
    paragraphs — verified across every file in both editions — so a markdown
    library would be a dependency bought for nothing. If the corpus ever grows
    richer syntax, replace this rather than extending it."""
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^##\s+(.+)$", block)
        if m:
            out.append(f"        <h2>{escape(m.group(1).strip())}</h2>")
        else:
            out.append(f'        <p>{escape(" ".join(l.strip() for l in block.split(chr(10))))}</p>')
    return "\n".join(out)


def field_rows(doc: dict, fm: dict, loc: dict) -> str:
    """The index record, rendered with provenance.

    `title` and `keywords` are author_declared because this corpus's
    frontmatter declares them. `summary` is read out of the body by the
    extractor, so it is labeled derived. That distinction is the entire reason
    the metadata carries source tags at all.
    """
    t = loc["docPage"]
    declared_kw = isinstance(fm.get("keywords"), list) and fm.get("keywords")

    def row(label, value, source):
        cls = "src-author" if source == "author" else "src-system"
        nice = t["authorDeclared"] if source == "author" else t["systemInferred"]
        return (
            '        <div class="field">\n'
            f'          <dt>{escape(label)}<span class="src {cls}">{escape(nice)}</span></dt>\n'
            f'          <dd>{value}</dd>\n'
            '        </div>'
        )

    def chips(items):
        return " ".join(f'<code class="chip-val">{escape(i)}</code>' for i in items) or f'<em>{escape(t["none"])}</em>'

    rows = [
        row("id", f'<code class="chip-val">{escape(doc["i"])}</code>', "author"),
        row("title", escape(doc.get("t", "")), "author"),
        row("summary", escape(doc.get("s", "")), "system"),
        row("headings", chips(doc.get("h") or []), "author"),
        row("keywords", chips(doc.get("k") or []), "author" if declared_kw else "system"),
    ]
    if doc.get("d"):
        rows.append(row("date", escape(doc["d"]), "author"))
    return "\n".join(rows)


def related_block(doc: dict, by_id: dict, loc: dict) -> str:
    """Curated links only — never similarity, never a guess.

    This is exactly the data the relation channel reads. Seeing it on the page
    is what makes a Tier D "same series as a confirmed match" result checkable
    rather than something the reader has to take on faith.
    """
    rel = doc.get("r") or []
    if not rel:
        return ""
    t = loc["docPage"]
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
                url=escape(doc_url(loc, target_id), quote=True),
                tid=escape(target_id),
                title=escape(target.get("t", "")),
            )
        )
    if not items:
        return ""
    return (
        '    <aside class="related">\n'
        f'      <h2>{t["relatedH2"]}</h2>\n'
        f'      <p class="related-intro">{t["relatedIntro"]}</p>\n'
        '      <ul>\n' + "\n".join(items) + '\n      </ul>\n'
        '    </aside>'
    )


def build_document_pages(index: dict, template: str, loc: dict, other: dict) -> int:
    by_id = {d["i"]: d for d in index.get("documents", [])}
    docs_dir = DEMO / ("docs" if loc["corpusLocale"] == "zh-Hant" else "docs-en")
    written = 0

    for doc in index.get("documents", []):
        doc_id = doc["i"]
        md_path = docs_dir / f"{doc_id}.md"
        if not md_path.exists():
            sys.exit(f"[drvs-site] no source markdown for indexed document '{doc_id}' ({md_path})")

        fm, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))

        meta_bits = [b for b in (doc.get("d"), fm.get("language")) if b]
        meta_bits.append(loc["docPage"]["corpusNote"])

        # The same document in the other language, so the toggle keeps you on
        # the page you were reading instead of dumping you at the home page.
        other_href = doc_url(other, doc_id)

        page = fill(template, loc)
        page = (
            page
            .replace("{{TITLE}}", escape(doc.get("t", "")))
            .replace("{{DOC_ID}}", escape(doc_id))
            .replace("{{SUMMARY_ATTR}}", escape(doc.get("s", ""), quote=True))
            .replace("{{META}}", escape(" · ".join(meta_bits)))
            .replace("{{BODY}}", render_markdown(body))
            .replace("{{EXTRACTED}}", field_rows(doc, fm, loc))
            .replace("{{RELATED}}", related_block(doc, by_id, loc))
            .replace("{{OTHER_DOC_HREF}}", escape(other_href, quote=True))
            .replace("{{docLang}}", escape(fm.get("language") or loc["htmlLang"], quote=True))
            .replace("{{otherLocaleLang}}", escape(other["htmlLang"], quote=True))
        )

        left = unresolved(page)
        if left:
            sys.exit(f"[drvs-site] unresolved placeholders in {doc_id} ({loc['locale']}): {left}")

        out_path = DIST / doc_url(loc, doc_id).strip("/") / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page, encoding="utf-8")
        written += 1

    return written


# ------------------------------------------------------------------- config

def build_locale_config(locale: str) -> dict:
    base = json.loads((ROOT / "config" / "search.config.json").read_text(encoding="utf-8"))
    base["labels"] = LABELS[locale]
    base["relationTypes"] = RELATION_TYPES[locale]
    base["note"] = ("Demo config for drvs.evemisslab.com — the package default plus this "
                    "locale's reason labels, supplied as JSON to exercise the same config "
                    "path a real deployment uses.")
    return base


# -------------------------------------------------------------------- build

def build_locale(loc: dict, other: dict, index_template: str, doc_template: str) -> dict:
    locale = loc["corpusLocale"]
    src_dist = DEMO / "dist" / locale
    index_path = src_dist / "index.json"
    if not index_path.exists():
        sys.exit(f"[drvs-site] missing {index_path} — run examples/demo-corpus/build_demo.py first")

    prefix = loc["dir"].strip("/")
    data_dir = DIST / prefix / "drvs" / "data" if prefix else DIST / "drvs" / "data"

    # corpus artifacts
    skipped = []
    for name, required in DEMO_DATA:
        src = src_dist / name
        if src.exists():
            copy(src, data_dir / name)
        elif required:
            sys.exit(f"[drvs-site] required demo artifact missing: {src}")
        else:
            skipped.append(name)

    # Rewrite the index's URLs into this locale's route space, so a link in
    # the corpus list can never point at a page this build didn't write.
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for d in index.get("documents", []):
        d["u"] = doc_url(loc, d["i"])
    (data_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (data_dir / "search.config.json").write_text(
        json.dumps(build_locale_config(locale), ensure_ascii=False, indent=2), encoding="utf-8")

    # the page itself
    page = fill(index_template, loc)
    alternates = (
        f'<link rel="alternate" hreflang="{loc["htmlLang"]}" href="https://drvs.evemisslab.com{loc["dir"]}">\n'
        f'<link rel="alternate" hreflang="{other["htmlLang"]}" href="https://drvs.evemisslab.com{other["dir"]}">\n'
        '<link rel="alternate" hreflang="x-default" href="https://drvs.evemisslab.com/">'
    )
    page = (
        page
        .replace("{{ALTERNATES}}", alternates)
        .replace("{{FACTS}}", render_facts(loc))
        .replace("{{CHIPS}}", render_chips(loc))
        .replace("{{WEDGE}}", render_wedge(loc))
        .replace("{{CARDS}}", render_cards(loc))
        .replace("{{DOCUMENT_ROWS}}", render_rows(index, loc))
        .replace("{{otherLocaleLang}}", escape(other["htmlLang"], quote=True))
        # Where this locale's corpus artifacts live. app.js reads it off the
        # demo root rather than hardcoding a path, so the same script serves
        # both locales.
        .replace("{{DATA_BASE}}", escape(f"{loc['dir'].rstrip('/')}/drvs/data", quote=True))
    )
    left = unresolved(page)
    if left:
        sys.exit(f"[drvs-site] unresolved placeholders in index ({loc['locale']}): {left}")

    out_index = (DIST / prefix / "index.html") if prefix else (DIST / "index.html")
    out_index.parent.mkdir(parents=True, exist_ok=True)
    out_index.write_text(page, encoding="utf-8")

    written = build_document_pages(index, doc_template, loc, other)
    return {"locale": loc["locale"], "documents": written, "skipped_artifacts": skipped}


def main():
    locales = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(I18N.glob("*.json"))]
    if len(locales) != 2:
        sys.exit(f"[drvs-site] expected exactly 2 locale bundles, found {len(locales)}")
    by_locale = {l["locale"]: l for l in locales}

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    # 1. static assets (files starting with "_" are templates, not assets)
    for path in SRC.rglob("*"):
        if path.is_file() and path.name != "index.html" and not path.name.startswith("_"):
            copy(path, DIST / path.relative_to(SRC))

    # 2. the real package
    missing = [rel for rel, _ in PACKAGE_FILES if not (ROOT / rel).exists()]
    if missing:
        sys.exit(f"[drvs-site] package files missing: {missing}")
    for rel, dest in PACKAGE_FILES:
        copy(ROOT / rel, DIST / dest)

    index_template = (SRC / "index.html").read_text(encoding="utf-8")
    doc_template = (SRC / "_document.html").read_text(encoding="utf-8")

    results = []
    for loc in locales:
        other = next(l for l in locales if l["locale"] != loc["locale"])
        results.append(build_locale(loc, other, index_template, doc_template))

    # sitemap, generated from what was just written rather than from a list —
    # the demo documents carry noindex and are excluded automatically.
    subprocess.run(
        [sys.executable, str(ROOT / "site" / "tools" / "gen_sitemap.py"),
         "--root", str(DIST), "--origin", "https://drvs.evemisslab.com"],
        check=True,
    )

    print(f"[drvs-site] built -> {DIST}")
    for r in results:
        note = f"  (optional artifacts absent: {r['skipped_artifacts']})" if r["skipped_artifacts"] else ""
        print(f"[drvs-site] {r['locale']}: {r['documents']} document pages{note}")
    print(f"[drvs-site] locales: {', '.join(by_locale)}")


if __name__ == "__main__":
    main()
