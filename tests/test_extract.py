#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS test suite — build/extract.py.

The summary extractor is the interesting part. "First paragraph of the file"
is trivial to write and looks correct on hand-written fixtures; it falls apart
on real corpora, where the first few lines are routinely a Word HTML-export
fragment, a metadata label line, an equation, or the title restated in bold.
Each case below pins one of those real failure modes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from build.extract import (
    parse_frontmatter,
    extract_headings,
    extract_summary,
    extract_keywords,
    extract_document,
    compact_document,
)

pass_count = 0
fail_count = 0


def check(label, cond, detail=""):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f"[PASS] {label}")
    else:
        fail_count += 1
        print(f"[FAIL] {label}{(chr(10) + '       ' + detail) if detail else ''}")


# --- frontmatter -------------------------------------------------------------
FM_DOC = """---
title: A Title
date: 2026-07-28
keywords:
  - alpha
  - beta
---
Body text starts here and is long enough to be a real summary paragraph.
"""
fm, body = parse_frontmatter(FM_DOC)
check("frontmatter scalar is parsed", fm.get("date") == "2026-07-28", f"got={fm}")
check("frontmatter list is parsed", fm.get("keywords") == ["alpha", "beta"], f"got={fm}")
check("body excludes the frontmatter block", body.strip().startswith("Body text"), f"got={body[:40]!r}")

no_fm, no_fm_body = parse_frontmatter("Just a body, no frontmatter.\n")
check("a file with no frontmatter returns an empty dict and the whole text",
      no_fm == {} and no_fm_body.startswith("Just a body"))

# A malformed block must degrade, never raise — one bad file cannot be allowed
# to abort a build that is walking an entire corpus.
try:
    parse_frontmatter("---\n\tnot: valid: yaml: at all\n:::\n---\nBody\n")
    check("malformed frontmatter never raises", True)
except Exception as e:
    check("malformed frontmatter never raises", False, f"raised {e!r}")

# --- headings ----------------------------------------------------------------
HEAD_DOC = "# Title\n\n## First\ntext\n\n### Second\ntext\n\n## First\nduplicate heading\n"
heads = extract_headings(HEAD_DOC)
check("## and ### headings are collected", heads[:2] == ["First", "Second"], f"got={heads}")
check("duplicate headings are not repeated", heads.count("First") == 1, f"got={heads}")
check("the h1 title is not collected as a heading", "Title" not in heads, f"got={heads}")
check("heading limit is respected",
      len(extract_headings("\n".join(f"## H{i}\ntext" for i in range(20)), limit=3)) == 3)

# --- summary: the real-world skip rules --------------------------------------
check("a plain opening paragraph is used as-is",
      extract_summary("This is the opening paragraph of the document.").startswith("This is the opening"))

check("markdown headings are skipped",
      extract_summary("## Section\n\nThe real opening paragraph.").startswith("The real opening"))

check("fenced code blocks are skipped entirely",
      extract_summary("```python\nprint('not a summary')\n```\n\nThe real opening paragraph.")
      .startswith("The real opening"))

check("tables, quotes, lists and display math are skipped",
      extract_summary("| a | b |\n> quoted\n- item\n$$x=1$$\n\nThe real opening paragraph.")
      .startswith("The real opening"))

# Word/HTML export debris — whole-line and mid-line forms are handled differently
# on purpose: a whole-line fragment is skipped, but a fragment embedded in a real
# sentence must be cut out so the sentence survives.
check("whole-line HTML export debris is skipped",
      extract_summary("<![if !msEquation]>\n<!--[if gte mso 9]>\n\nThe real opening paragraph.")
      .startswith("The real opening"))
inline = extract_summary("A real sentence <![if !msEquation]> continues after the debris.")
check("mid-line HTML debris is cut out, keeping the surrounding sentence",
      "msEquation" not in inline and "A real sentence" in inline and "continues after" in inline,
      f"got={inline!r}")
check("an inline base64 data-image is removed",
      "data:image" not in extract_summary("Text ![eq](data:image/png;base64,AAAA) more text here."))

check("a standalone Abstract: label line is skipped",
      extract_summary("摘要\n\nThe real opening paragraph.").startswith("The real opening"))

check("a short metadata label line is skipped",
      extract_summary("Author: Neo.K\n\nThe real opening paragraph.").startswith("The real opening"))

check("a short colon-terminated introducer line is skipped",
      extract_summary("其中：\n\nThe real opening paragraph.").startswith("The real opening"))

# The title-echo rule: many documents restate their own title as the first body
# line. Using that as the summary makes every card read "Title — Title".
echo = extract_summary("Flattened Dimensional Reconstructive Theory\n\nThe real opening paragraph.",
                       title="Flattened Dimensional Reconstructive Theory")
check("a first line that just restates the title is skipped",
      echo.startswith("The real opening"), f"got={echo!r}")

check("inline LaTeX is stripped from the summary",
      "$" not in extract_summary("The value $x = 1$ matters here in this opening paragraph."))

long_summary = extract_summary("word " * 200, limit=50)
check("summary is truncated to the limit with an ellipsis",
      len(long_summary) <= 51 and long_summary.endswith("…"), f"len={len(long_summary)}")

check("a body with no usable prose returns an empty summary, not garbage",
      extract_summary("## Only\n\n## Headings\n\n- and\n- lists") == "")

# --- keywords + provenance ---------------------------------------------------
kw, src = extract_keywords("任何標題", declared=["alpha", "beta"])
check("author-declared keywords are used verbatim", kw == ["alpha", "beta"])
check("author-declared keywords are tagged author_declared", src == "author_declared")

kw2, src2 = extract_keywords("量子計算的研究方法")
check("derived keywords are tagged system_inferred, never author_declared", src2 == "system_inferred")
check("stopwords are excluded from derived keywords",
      "研究" not in kw2 and "方法" not in kw2, f"got={kw2}")

kw3, src3 = extract_keywords("A Study of Quantum Computing Systems")
check("Latin stopwords are excluded case-insensitively",
      not {"a", "of", "study"} & {k.lower() for k in kw3}, f"got={kw3}")

# --- whole-document extraction ----------------------------------------------
doc = extract_document(
    "doc-1", "Quantum Notes", "/d/doc-1/",
    "---\ndate: 2026-07-28\n---\n## Intro\n\nThe opening paragraph of this document.\n",
)
check("extract_document returns the caller's id/title/url", doc["id"] == "doc-1" and doc["title"] == "Quantum Notes")
check("extract_document picks up frontmatter date", doc["date"] == "2026-07-28")
check("extract_document extracts headings and summary",
      doc["headings"] == ["Intro"] and doc["summary"].startswith("The opening"), f"got={doc}")
check("every extracted field carries a provenance tag",
      set(doc["metadata"]) >= {"title_source", "summary_source", "keywords_source", "headings_source"})
check("a derived summary is never claimed as author_declared",
      doc["metadata"]["summary_source"] == "system_inferred")

# --- compaction --------------------------------------------------------------
c = compact_document(doc)
check("compact form uses short keys", c["i"] == "doc-1" and c["t"] == "Quantum Notes")
check("compact form omits empty fields entirely", "r" not in c, f"got={c}")

c2 = compact_document({**doc, "related_ids": ["doc-2"]})
check("a bare related id is normalised to an [id, type] pair",
      c2["r"] == [["doc-2", "s"]], f"got={c2.get('r')}")
c3 = compact_document({**doc, "relations": [["doc-2", "p"]]})
check("an explicit [id, type] relation is preserved as-is",
      c3["r"] == [["doc-2", "p"]], f"got={c3.get('r')}")

print(f"\n--- {pass_count}/{pass_count + fail_count} passed ---")
sys.exit(0 if fail_count == 0 else 1)
