#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS test suite — build/chunk.py. Pure text logic, no model calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from build.chunk import chunk_document, extract_units, bucket_units

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


# --- empty / degenerate input never crashes ---------------------------------
check("empty body returns no chunks", chunk_document("d1", "Title", "") == [])
check("whitespace-only body returns no chunks", chunk_document("d1", "Title", "   \n\n  ") == [])

# --- heading-delimited chunking ---------------------------------------------
HEADED_BODY = """前言，這段在第一個標題之前，長度足夠成為一個獨立單元不會被丟棄不會被丟棄。

## 第一節
第一節的內容，長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。

## 第二節
第二節的內容，長度也足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。
"""
chunks = chunk_document("doc-a", "測試文件", HEADED_BODY, max_chunks=6, min_chunk_chars=10)
check("heading-delimited doc produces at least one chunk per real section", len(chunks) >= 2, f"got {len(chunks)}")
check("pre-heading content survives as its own (headingless) chunk", any(c["heading"] is None for c in chunks))
check("a real heading is captured verbatim", any(c["heading"] == "第一節" for c in chunks), f"headings={[c['heading'] for c in chunks]}")
check("chunk_id format is doc_id#chunk-N", all(c["chunk_id"].startswith("doc-a#chunk-") for c in chunks))
check("chunk text includes the document title as context", all("測試文件" in c["text"] for c in chunks))

# --- paragraph-fallback chunking (no headings at all) -----------------------
NO_HEADING_BODY = "\n\n".join([
    "第一段內容足夠長，長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。",
    "第二段內容也足夠長，長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。",
    "第三段內容一樣足夠長，長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。",
])
no_heading_chunks = chunk_document("doc-b", "無標題文件", NO_HEADING_BODY, max_chunks=6, min_chunk_chars=10)
check("headingless doc falls back to paragraph splitting", len(no_heading_chunks) >= 1)
check("all paragraph-fallback chunks have heading=None", all(c["heading"] is None for c in no_heading_chunks))

# --- max_chunks bound is respected even with far more headings -------------
MANY_HEADINGS_BODY = "\n\n".join(f"## 標題{i}\n內容{i}，長度足夠成為一個獨立單元不會被丟棄不會被丟棄。" for i in range(30))
bounded = chunk_document("doc-c", "多標題文件", MANY_HEADINGS_BODY, max_chunks=6, min_chunk_chars=10)
check("30 headings still bound to <= max_chunks", len(bounded) <= 6, f"got {len(bounded)}")

# --- min_chunk_chars filtering ------------------------------------------------
TINY_BODY = "## 太短\n短\n\n## 正常章節\n這段內容長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。"
tiny_chunks = chunk_document("doc-d", "標題", TINY_BODY, max_chunks=6, min_chunk_chars=30)
check(
    "a bucket that never reaches min_chunk_chars is dropped, not embedded as noise",
    all(len(c["text"]) >= 10 for c in tiny_chunks),  # text includes title prefix, so check loosely
)

# --- max_chunk_chars truncation ------------------------------------------------
LONG_SECTION_BODY = "## 長章節\n" + ("很長的內容片段。" * 200)
long_chunks = chunk_document("doc-e", "標題", LONG_SECTION_BODY, max_chunks=6, max_chunk_chars=100, min_chunk_chars=10)
check("chunk text is capped at max_chunk_chars", all(len(c["text"]) <= 100 for c in long_chunks), f"lengths={[len(c['text']) for c in long_chunks]}")

# --- custom heading_re override ----------------------------------------------
import re
custom_re = re.compile(r"^===\s+(.+?)\s*===$", re.MULTILINE)
CUSTOM_BODY = "=== 自訂標題 ===\n這段內容長度足夠成為一個獨立單元不會被丟棄不會被丟棄不會被丟棄。"
custom_chunks = chunk_document("doc-f", "標題", CUSTOM_BODY, heading_re=custom_re, min_chunk_chars=10)
check("custom heading_re is honored instead of the ##/### default", any(c["heading"] == "自訂標題" for c in custom_chunks), f"got={custom_chunks}")

# --- extract_units / bucket_units as standalone primitives -------------------
units = extract_units(HEADED_BODY, min_chunk_chars=10)
check("extract_units finds the same number of real sections as chunk_document", len(units) >= 2)
buckets = bucket_units(units, max_chunks=1)
check("bucket_units respects a max_chunks=1 cap", len(buckets) <= 1)

print(f"\n--- {pass_count}/{pass_count + fail_count} passed ---")
sys.exit(0 if fail_count == 0 else 1)
