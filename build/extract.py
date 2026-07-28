#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS — Dynamic Revealing Vector Search
build/extract.py: turn raw markdown-ish source files into Document records.

Writing the adapter that maps YOUR corpus onto the Document schema is the one
part of DRVS that is always bespoke (see docs/ADAPTING_A_CORPUS.md). This
module is the reusable middle of that job: the file-level text work that is
the same regardless of where your documents live or what your ids look like.

Nothing here is clever. What it is, is *specific about real-world mess*. Every
skip rule below exists because a real corpus produced a genuinely bad summary
without it — a Word HTML export leaking `<![if !msEquation]>` fragments into
the first paragraph, an equation exported as an inline base64 PNG, a document
whose opening line just restates its own title, a "Where:" introducer line
pointing at an equation that follows. A naive "first paragraph" summary
extractor looks fine on hand-written test fixtures and falls apart on the
first few hundred real documents.

Two rules run through all of it:

  * **Never invent.** A summary is real text lifted from the document. A
    keyword is either author-declared or transparently derived from the title.
    Nothing here writes prose about a document.

  * **Always say where a field came from.** Every extracted field is returned
    alongside a source tag (`author_declared` vs `system_inferred`) so a
    heuristic guess is never silently presented as something the author
    asserted. Carry those tags into your index and keep them there.
"""
import re
from datetime import datetime, timezone

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
FM_KV_RE = re.compile(r"^([A-Za-z_]+):\s*(.*)$")
FM_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)
MD_STRIP_RE = re.compile(r"[*_`#>]")
LATEX_INLINE_RE = re.compile(r"\$[^$\n]{1,80}\$")
LABEL_LINE_RE = re.compile(r"^[A-Za-z一-鿿 ]{1,14}[:：]\s*\S")
CJK_RUN_RE = re.compile(r"[一-鿿]{2,6}")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]{1,}")

# Word/HTML-export leftovers: stray MSO conditional-comment fragments
# (<!--[if ...]>, <![endif]-->) and stray tags occupying a whole line.
HTML_LINE_RE = re.compile(r"^</?[a-zA-Z!]|^<!--|^<!\[|-->$|^-->")
# The same leftovers occurring MID-line (an inline "<![if !msEquation]>"
# placeholder, a Word equation exported as an embedded base64 PNG) — these
# have to be cut out of the line, not cause the line to be skipped, or you
# lose the real sentence wrapped around them.
INLINE_HTML_RE = re.compile(r"<!--.*?-->|<!\[if[^\]]*\]>?|<!\[endif\]>?|<!\[vml\]>?")
INLINE_DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:image[^)]*\)")

ABSTRACT_LABEL_RE = re.compile(r"^(摘要|abstract|summary)[:：]?$", re.IGNORECASE)
# A short line ending in a colon or question mark with nothing after it is a
# section introducer ("Where:", "本文提出：") pointing at the list/equation/quote
# that follows — not summary-worthy prose on its own.
INTRODUCER_LINE_RE = re.compile(r"[:：?？]$")
_COMPARE_STRIP_RE = re.compile(r"[\s*_`「」『』《》〈〉“”‘’．。，、：:；;\-—－()（）\[\]]+")

# Generic words that carry no distinguishing signal as a keyword. Extend or
# replace for your corpus and language — a legal archive wants a very
# different list from a research corpus.
DEFAULT_TITLE_STOPWORDS = {
    "一種", "研究", "初步", "框架", "模型", "理論", "系統", "分析", "方法",
    "白皮書", "技術", "本地", "文件", "論文", "草稿", "報告", "綱領", "系列",
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
    "toward", "towards", "using", "study", "notes", "draft", "report",
    "introduction", "overview", "framework", "model", "system", "analysis",
    "method", "methods", "approach", "paper", "whitepaper",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_frontmatter(text: str):
    """Split leading `---` frontmatter from the body.

    Deliberately a tiny hand-rolled subset of YAML — `key: value` and
    `key:` followed by `  - item` lists — with NO dependency on a YAML
    library. A build pipeline that walks an entire corpus should not fail on
    one document with slightly malformed frontmatter, and a real YAML parser
    raising on a stray tab is exactly that failure. Anything it can't parse is
    simply not extracted.

    Returns (frontmatter_dict, body_text).
    """
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
                    items.append(im.group(1).strip().strip('"'))
                    j += 1
                if items:
                    fm[key] = items
                    i = j
                    continue
            else:
                fm[key] = val.strip('"')
        i += 1
    return fm, body


def extract_headings(body: str, limit: int = 6) -> list:
    """Up to `limit` distinct ##/### headings, markdown markers stripped."""
    heads = []
    for hm in HEADING_RE.finditer(body):
        h = MD_STRIP_RE.sub("", hm.group(1).strip())
        if h and h not in heads:
            heads.append(h[:60])
        if len(heads) >= limit:
            break
    return heads


def _compare_key(s: str) -> str:
    """Punctuation/whitespace-insensitive key for 'is this line just the title
    again?' comparisons."""
    return _COMPARE_STRIP_RE.sub("", s).lower()


def extract_summary(body: str, title: str = "", limit: int = 180) -> str:
    """The document's first real paragraph of prose, cleaned and truncated.

    "Real" is doing the work here — the loop skips headings, tables, fenced
    code, block quotes, list items, display math, bare image lines, HTML export
    debris, standalone "Abstract:" labels, short colon-terminated introducer
    lines, and any line that merely restates the title. What survives is the
    first thing a human would actually read as a description.
    """
    title_key = _compare_key(title)
    para, in_fence = [], False
    for raw_line in body.split("\n"):
        s = raw_line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not s:
            if para:
                break  # blank line after real content ends the first paragraph
            continue
        if HTML_LINE_RE.search(s):
            continue
        if s.startswith(("#", "|", "$$", ">", "-", "*", "![")) or re.match(r"^\d+\.", s):
            continue
        if ABSTRACT_LABEL_RE.match(s):
            continue
        s = INLINE_DATA_IMAGE_RE.sub("", s)
        s = INLINE_HTML_RE.sub("", s)
        candidate = MD_STRIP_RE.sub("", s).strip()
        if not candidate:
            continue
        # "Author: ...", "Version: ..." metadata lines masquerading as prose.
        if len(candidate) < 40 and LABEL_LINE_RE.match(candidate):
            continue
        if not para and len(candidate) < 16 and INTRODUCER_LINE_RE.search(candidate):
            continue
        # A bolded title echo whose markdown markers were already stripped, so
        # the "*" prefix check above could not catch it.
        ck = _compare_key(candidate)
        if title_key and ck and len(ck) < len(title_key) + 12 and (ck in title_key or title_key in ck):
            continue
        para.append(candidate)
        if len(" ".join(para)) > limit * 2:
            break

    text = " ".join(para)
    text = LATEX_INLINE_RE.sub("", text)
    # U+FEFF is not whitespace in Python's unicode tables, so a BOM survives
    # .strip() and shows up as a leading blank glyph in the rendered summary.
    text = re.sub(r"\s+", " ", text).strip().lstrip("﻿")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def extract_keywords(title: str, declared=None, limit: int = 6, stopwords=None):
    """Keywords plus their provenance.

    Author-declared keywords are used verbatim when present. Otherwise they are
    derived from the title — CJK runs and Latin tokens, minus stopwords — and
    tagged `system_inferred`, never passed off as the author's own.

    Returns (keywords, source_tag).
    """
    if declared:
        return [k[:24] for k in list(declared)[:limit] if k], "author_declared"
    stops = stopwords if stopwords is not None else DEFAULT_TITLE_STOPWORDS
    toks = [t for t in CJK_RUN_RE.findall(title) if t not in stops]
    toks += [t for t in LATIN_TOKEN_RE.findall(title) if t.lower() not in stops]
    seen: list = []
    for t in toks:
        if t not in seen:
            seen.append(t)
    return seen[:limit], "system_inferred"


def extract_document(doc_id, title, url, raw_text, date=None, language=None,
                     summary_limit=180, heading_limit=6, keyword_limit=6,
                     stopwords=None, related_ids=None) -> dict:
    """One raw source file -> one full Document record (schema/document.md).

    `title` wins over any frontmatter title: the caller's title comes from the
    registry/filesystem, which is the stable identity users and URLs already
    agree on. Frontmatter fills in only what the caller didn't supply.
    """
    fm, body = parse_frontmatter(raw_text or "")
    headings = extract_headings(body, heading_limit)
    summary = extract_summary(body, title or "", summary_limit)
    declared = fm.get("keywords") if isinstance(fm.get("keywords"), list) else None
    keywords, kw_source = extract_keywords(title or "", declared, keyword_limit, stopwords)

    return {
        "id": doc_id,
        "title": title,
        "url": url,
        "date": date or fm.get("date"),
        "language": language or fm.get("language"),
        "summary": summary,
        "headings": headings,
        "keywords": keywords,
        "related_ids": list(related_ids or []),
        "metadata": {
            "title_source": "author_declared",
            "summary_source": "system_inferred",
            "keywords_source": kw_source,
            "headings_source": "author_declared",
            "schema_version": "0.1",
        },
    }


def compact_document(doc: dict) -> dict:
    """Full Document -> the short-key form the browser index ships.

    Keys are single letters because this file is fetched by every visitor and
    the field names would otherwise be a meaningful fraction of its bytes over
    a few thousand documents. Provenance and long-form fields stay in your raw
    build output, which nobody downloads.

    Relations are emitted as [id, type_code] pairs; see the relation-type
    vocabulary in core/scoring.js.
    """
    out = {
        "i": doc["id"],
        "t": doc.get("title") or "",
        "u": doc.get("url") or "",
    }
    if doc.get("date"):
        out["d"] = doc["date"]
    if doc.get("summary"):
        out["s"] = doc["summary"]
    if doc.get("headings"):
        out["h"] = doc["headings"]
    if doc.get("keywords"):
        out["k"] = doc["keywords"]
    rel = doc.get("relations") or doc.get("related_ids") or []
    if rel:
        out["r"] = [r if isinstance(r, (list, tuple)) else [r, "s"] for r in rel]
    return out
