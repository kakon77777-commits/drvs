#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS — Dynamic Revealing Vector Search
build/chunk.py: corpus-agnostic section/paragraph chunking.

A document-level embedding (title + summary + headings) captures what a
document is ABOUT; it can't tell you WHICH passage matches a specific query
in a long document. This module splits a document's body text into a small,
bounded number of chunks so a complementary per-section embedding can also
surface "this specific paragraph is the match" precision (see build/embed.py
for turning chunks into vectors, aggregated back to a per-document score via
max(chunk similarities)).

Chunking strategy: a real-world corpus's heading density is usually wildly
uneven -- some documents have zero markdown headings, others have over a
hundred -- so "one chunk per heading" is not a safe default; a heading-heavy
document would explode into far more chunks (and far more embedding calls)
than a heading-light one, with no cap. Instead: extract natural text units
(heading-delimited sections where headings exist, paragraph breaks
otherwise), then greedily bucket them by cumulative length into AT MOST
max_chunks buckets, preserving document order. This bounds the chunk count
per document regardless of heading density, while a normal few-section
document gets close to one chunk per section. Each chunk's text is capped at
max_chunk_chars so the embedding input stays a reasonable, consistent size
regardless of how much raw text a bucket accumulated.
"""
import re

MD_STRIP_RE = re.compile(r"[*_`#>]")
DEFAULT_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
PARA_SPLIT_RE = re.compile(r"\n\s*\n")

DEFAULT_MAX_CHUNKS_PER_DOC = 6
DEFAULT_MAX_CHUNK_CHARS = 800
DEFAULT_MIN_CHUNK_CHARS = 30  # shorter than this isn't worth its own embedding call


def _clean(text: str) -> str:
    text = MD_STRIP_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_units(body: str, heading_re=None, min_chunk_chars=DEFAULT_MIN_CHUNK_CHARS) -> list:
    """(heading_or_None, text) per natural unit -- heading-delimited section
    if the doc has any headings matching `heading_re`, else paragraph-
    delimited (split on blank lines). A custom `heading_re` must capture the
    heading TEXT in its LAST group (`.groups()[-1]`) -- e.g. `^(#{2,3})\\s+
    (.+?)\\s*$` (default: markdown ##/###) or `^===\\s+(.+?)\\s*===$` (a
    single-group alternative) both work; the marker/prefix, if any, can be
    an earlier group that's simply ignored."""
    heading_re = heading_re or DEFAULT_HEADING_RE
    headings = list(heading_re.finditer(body))
    units = []
    if headings:
        # content before the first heading, if any, is still real text
        if headings[0].start() > 0:
            pre = _clean(body[: headings[0].start()])
            if len(pre) >= min_chunk_chars:
                units.append((None, pre))
        for i, hm in enumerate(headings):
            start = hm.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            heading_text = _clean(hm.groups()[-1])
            content = _clean(body[start:end])
            if content or heading_text:
                units.append((heading_text or None, content))
    else:
        for para in PARA_SPLIT_RE.split(body):
            cleaned = _clean(para)
            if len(cleaned) >= min_chunk_chars:
                units.append((None, cleaned))
    return units


def bucket_units(units: list, max_chunks=DEFAULT_MAX_CHUNKS_PER_DOC) -> list:
    """Greedy length-balanced grouping into <= max_chunks buckets, preserving
    order. Returns [(heading_or_None, joined_text), ...].

    A bucket's heading is whichever unit's heading was the FIRST one actually
    placed into THAT bucket -- decided only once the flush decision for the
    incoming unit has already been made, not "peeked" from the incoming unit
    before deciding whether it even joins the current bucket. Getting this
    order backwards (checking the incoming unit's heading before the flush
    check) silently mislabels the case that matters most: a document that
    opens with real pre-heading content (an intro/abstract paragraph before
    its first `##`) gets that intro merged into the SAME decision step as
    the first real heading, so the intro's bucket inherits that heading's
    name instead of staying `None` -- caught by a test asserting pre-heading
    content survives as its own headingless chunk, not by inspection.
    """
    if not units:
        return []
    total_len = sum(len(t) for _, t in units)
    target = max(1, total_len // max_chunks)

    buckets = []
    cur_heading = None
    cur_heading_set = False
    cur_parts = []
    cur_len = 0
    for heading, text in units:
        if cur_parts and cur_len + len(text) > target and len(buckets) < max_chunks - 1:
            buckets.append((cur_heading, " ".join(cur_parts)))
            cur_heading, cur_heading_set, cur_parts, cur_len = None, False, [], 0
        if not cur_heading_set and heading:
            cur_heading = heading
            cur_heading_set = True
        cur_parts.append(text)
        cur_len += len(text)
    if cur_parts:
        buckets.append((cur_heading, " ".join(cur_parts)))
    return buckets[:max_chunks]


def _chunk_text(doc_title: str, heading, body_text: str, max_chunk_chars: int) -> str:
    """What actually gets embedded -- title context + section heading (if
    any) + the section's own text, so a chunk embedded in isolation still
    carries which document/section it's from (matters for short chunks whose
    own text alone is ambiguous)."""
    prefix = doc_title if not heading else f"{doc_title} — {heading}"
    text = f"{prefix}\n{body_text}"
    return text[:max_chunk_chars]


def chunk_document(
    doc_id: str,
    title: str,
    body_text: str,
    max_chunks: int = DEFAULT_MAX_CHUNKS_PER_DOC,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS,
    heading_re=None,
) -> list:
    """Split one document's body text into <= max_chunks chunks.

    `body_text` is plain body text -- frontmatter/metadata already stripped
    by the caller's corpus adapter; this module has no opinion on source
    format beyond "markdown-ish ## / ### headings, if any" (override
    `heading_re` for a different heading convention).

    Returns [{"chunk_id", "heading", "text"}, ...]. chunk_id is
    "{doc_id}#chunk-{i}"; heading is the matched section's heading text, or
    None for a paragraph-fallback chunk. Returns [] for empty/whitespace-only
    input -- never raises on bad input, since a build pipeline processing an
    entire corpus must not abort over one malformed document.
    """
    if not body_text or not body_text.strip():
        return []
    units = extract_units(body_text, heading_re=heading_re, min_chunk_chars=min_chunk_chars)
    buckets = bucket_units(units, max_chunks=max_chunks)
    chunks = []
    for i, (heading, text) in enumerate(buckets):
        if len(text) < min_chunk_chars:
            continue
        chunks.append({
            "chunk_id": f"{doc_id}#chunk-{i}",
            "heading": heading,
            "text": _chunk_text(title, heading, text, max_chunk_chars),
        })
    return chunks
