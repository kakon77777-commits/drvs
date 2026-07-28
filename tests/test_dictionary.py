#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS test suite — build/dictionary.py.

A dictionary is the highest-leverage place in this system to lie by accident:
a fabricated alias makes unrelated documents look like confirmed matches,
under the most authoritative label the UI has. So most of these cases are
about what the extractor must REFUSE to emit, not what it emits.

Every rejection case below corresponds to a real false positive that reached a
production dictionary in an earlier version and had to be caught by hand.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from build.dictionary import (
    build_dictionary,
    acronym_entries,
    curated_entries,
    extract_acronym_pairs,
    audit_candidates,
    clean_name,
    looks_like_roman_numeral,
    compact_dictionary,
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


def acronyms(entries):
    return {e["aliases"][0]["term"] for e in entries if e["aliases"]}


# --- self-defining titles are extracted in both orders ----------------------
DOCS = [
    {"id": "d1", "title": "FDRS_扁平維度重構理論_v2.1"},
    {"id": "d2", "title": "認知動力學_ETN：第一篇"},
    {"id": "d3", "title": "MCDM_多準則決策方法_v0.1"},
]
entries = acronym_entries(DOCS)
got = acronyms(entries)
check("acronym-first self-defining title is extracted", "FDRS" in got, f"got={got}")
check("name-first self-defining title is extracted", "ETN" in got, f"got={got}")
check("extracted canonical is the expansion, not the acronym",
      any(e["canonical"] == "扁平維度重構理論" for e in entries),
      f"canonicals={[e['canonical'] for e in entries]}")
check("every entry carries evidence naming its source document",
      all(e["evidence"].get("source_document_id") for e in entries))
check("every entry is system_inferred, never editor_confirmed",
      all(e["status"] == "system_inferred" for e in entries))

# --- Latin-expansion titles work too (not just CJK) -------------------------
latin = acronym_entries([{"id": "L1", "title": "FDRS_Flattened Dimensional Reconstructive Theory"}])
check("Latin-expansion self-defining title is extracted", "FDRS" in acronyms(latin), f"got={latin}")

# --- REJECTION: acronym sitting mid-prose, not defining itself --------------
# The original bug: a bare space was accepted as a boundary, so any capitalised
# loanword inside an ordinary sentence looked like a structured definition.
prose = acronym_entries([
    {"id": "p1", "title": "小模型開源訓練作為高 ROI 時代槓桿"},
    {"id": "p2", "title": "為什麼 AIAGI 需要正確的自我本體論"},
])
check("an acronym mid-sentence is NOT treated as a self-definition",
      acronyms(prose) == set(), f"wrongly extracted={acronyms(prose)}")

# --- REJECTION: roman numerals are series markers, not acronyms -------------
check("roman numeral detector recognises III", looks_like_roman_numeral("III"))
check("roman numeral detector recognises IV", looks_like_roman_numeral("IV"))
check("roman numeral detector does not reject a real acronym", not looks_like_roman_numeral("FDRS"))
roman = acronym_entries([{"id": "r1", "title": "III_無限維方向壓縮法_v0.1"}])
check("a roman-numeral series marker never becomes a concept",
      acronyms(roman) == set(), f"wrongly extracted={acronyms(roman)}")

# --- REJECTION: conflicting expansions are dropped, not first-wins ----------
conflict_docs = [
    {"id": "c1", "title": "PCMT_七十二格計算動力學_v0.1"},
    {"id": "c2", "title": "PCMT_二十四計算範式_v0.2"},
]
pairs, ambiguous = extract_acronym_pairs(conflict_docs)
check("an acronym with two different expansions is flagged ambiguous", "PCMT" in ambiguous)
check("an ambiguous acronym is dropped entirely, not resolved first-wins",
      "PCMT" not in pairs, f"pairs={pairs}")

# --- REJECTION: explicit exclusion list -------------------------------------
excluded = acronym_entries(DOCS, excluded={"FDRS"})
check("an explicitly excluded acronym is omitted", "FDRS" not in acronyms(excluded))
check("excluding one acronym does not drop the others", "ETN" in acronyms(excluded))

# A genuine surprise worth locking down: plenty of ordinary-looking 3-letter
# acronyms ARE valid roman numerals ("CDX" == 410, "DIM" is not but "MIX" is),
# so the series-marker filter silently swallows them. That is the correct
# trade — a fake concept is worse than a missing one — but it means an
# excluded-by-accident acronym must show up in audit output, never vanish.
cdx = acronym_entries([{"id": "n1", "title": "認知動力學_CDX：第一篇"}])
check("an acronym that happens to be a valid roman numeral is rejected",
      acronyms(cdx) == set(), f"wrongly extracted={acronyms(cdx)}")

# --- generic-suffix trimming, longest-first ---------------------------------
# The original bug: a shorter suffix that is the tail of a longer one re-trimmed
# an already-trimmed name into a meaningless fragment.
check("a name that is ONLY boilerplate is rejected outright", clean_name("技術白皮書") is None)
check("longest-suffix-first prevents re-trimming into a fragment",
      clean_name("量子計算技術白皮書") == "量子計算",
      f"got={clean_name('量子計算技術白皮書')!r}")
check("a name too short after trimming is rejected", clean_name("量子白皮書") is None,
      f"got={clean_name('量子白皮書')!r}")

# --- curated alias sets -----------------------------------------------------
curated = curated_entries([
    {"id": "prog-x", "canonical": "X 積分研究計畫", "aliases": ["X-Integral", {"term": "XIP", "weight": 0.95}]},
    {"id": "prog-empty", "canonical": "沒有別名的計畫", "aliases": []},
])
check("a curated alias set becomes one entry", len(curated) == 1, f"got={len(curated)}")
check("a curated set with no aliases is skipped (expanding a term to itself is useless)",
      all(e["canonical"] != "沒有別名的計畫" for e in curated))
check("curated string aliases and weighted-dict aliases both work",
      {a["term"] for a in curated[0]["aliases"]} == {"X-Integral", "XIP"},
      f"got={curated[0]['aliases']}")
check("a curated entry is also system_inferred, not editor_confirmed",
      curated[0]["status"] == "system_inferred")

# --- audit surface ----------------------------------------------------------
audit = audit_candidates(DOCS + conflict_docs, excluded={"FDRS"})
check("audit reports accepted candidates", audit["counts"]["accepted"] >= 1)
check("audit reports config-excluded candidates separately",
      "FDRS" in audit["excluded_by_config"], f"got={audit['excluded_by_config']}")
check("audit reports ambiguity-dropped candidates separately",
      "PCMT" in audit["dropped_ambiguous"], f"got={audit['dropped_ambiguous']}")

# --- compaction drops provenance from the shipped file ----------------------
full = build_dictionary(documents=DOCS, curated_alias_sets=[
    {"id": "prog-x", "canonical": "X 積分研究計畫", "aliases": ["X-Integral"]},
])
compact = compact_dictionary(full)
check("curated entries are ordered ahead of mechanically-extracted ones",
      full[0]["evidence"].get("method") == "curated_alias_set")
check("compact form keeps exactly the fields the scorer reads",
      all(set(e.keys()) == {"concept_id", "canonical", "aliases", "related"} for e in compact))
check("compact form drops provenance (it is for auditing, not for every visitor)",
      all("evidence" not in e and "status" not in e for e in compact))

# --- degenerate input never raises ------------------------------------------
check("empty document list returns no entries", build_dictionary(documents=[]) == [])
check("documents with no titles never raise",
      build_dictionary(documents=[{"id": "x", "title": ""}, {"id": "y"}]) == [])

print(f"\n--- {pass_count}/{pass_count + fail_count} passed ---")
sys.exit(0 if fail_count == 0 else 1)
