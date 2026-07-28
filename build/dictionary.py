#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS — Dynamic Revealing Vector Search
build/dictionary.py: concept dictionary for the query-expansion channel.

A dictionary entry lets a query for one term also search for its aliases, so
someone who types the English name of a concept, or its acronym, or the name
it used to have, still finds the documents that only ever use the other form.
core/scoring.js consumes the output via expandQuery(); without a dictionary
that channel is simply a no-op.

THE HARD RULE: every entry must trace back to real evidence.

A dictionary is the highest-leverage place in this whole system to lie by
accident. An invented alias doesn't fail loudly — it silently makes unrelated
documents look like confirmed matches, and it does so under the most
authoritative label the UI has ("matched known alias"). So this module only
generates entries from sources where the pairing is something an author
already wrote down, never something a model or a heuristic inferred:

  1. `curated_alias_sets` — alias sets you already maintain for another
     purpose (a series index, a project registry, a glossary). You are
     asserting these; this module just reshapes them.

  2. Self-defining titles — documents that name themselves "ACRONYM_Full Name"
     or "Full Name_ACRONYM". The acronym and its expansion appear PAIRED in
     the document's own title, so extracting the pair is reading what the
     author wrote, not guessing.

Every entry is tagged `system_inferred` and carries an `evidence` block naming
the document or curated record it came from. Nothing here is ever tagged
`editor_confirmed` — that status is reserved for a human actually reviewing
the dictionary, which this module cannot do on your behalf.

Extraction is mechanical, so it WILL produce garbage on a real corpus; the
filters below each exist because a specific, real false positive got through
an earlier version. Run `audit_candidates()` on your corpus and read the
output before shipping a generated dictionary — that is the intended workflow,
not an optional extra.
"""
import json
import re
from datetime import datetime, timezone

# The name capture must end at a real naming-construct boundary — a version
# marker (_v0.1), an underscore, a colon, or end of string — NOT a bare space.
# An earlier version allowed a bare space and happily matched any acronym
# sitting in the middle of an ordinary sentence ("... as a high ROI lever",
# "why AIAGI needs a correct self-ontology") as though it were a structured
# self-definition. Requiring a real boundary is what separates "MCDM_v0.1" (a
# title labeling itself) from "... high ROI era ..." (a loanword mid-prose).
_BOUNDARY = r"(?:_v\d|[_：:]|$)"

# CJK expansions (the origin corpus's convention).
CJK_ACRONYM_THEN_NAME_RE = re.compile(r"^([A-Z]{2,8})[_\s]([一-鿿]{4,20})" + _BOUNDARY)
CJK_NAME_THEN_ACRONYM_RE = re.compile(r"^([一-鿿]{3,24})[_\s]([A-Z]{2,8})" + _BOUNDARY)

# Latin expansions ("FDRS_Flattened Dimensional Reconstructive Theory",
# "Flattened Dimensional Reconstructive Theory_FDRS"). The name side requires
# at least two capitalised words, because a single word after an acronym is
# far more often prose than a definition.
#
# No `\b` before the name group: `_` is itself a word character, so there is
# no word boundary between the separator and the name, and asserting one makes
# the pattern silently never match.
LATIN_ACRONYM_THEN_NAME_RE = re.compile(r"^([A-Z]{2,8})[_]([A-Z][a-z]+(?:[ -][A-Za-z]+){1,6})" + _BOUNDARY)
LATIN_NAME_THEN_ACRONYM_RE = re.compile(r"^([A-Z][a-z]+(?:[ -][A-Za-z]+){1,6})[_]([A-Z]{2,8})" + _BOUNDARY)

DEFAULT_ACRONYM_PATTERNS = [
    ("acronym_first", CJK_ACRONYM_THEN_NAME_RE),
    ("name_first", CJK_NAME_THEN_ACRONYM_RE),
    ("acronym_first", LATIN_ACRONYM_THEN_NAME_RE),
    ("name_first", LATIN_NAME_THEN_ACRONYM_RE),
]

# Boilerplate a title tacks onto the real concept name. Sorted LONGEST FIRST
# and matched with an early break, because a shorter suffix that is itself the
# tail of a longer one will otherwise re-trim an already-trimmed name into
# nonsense. Observed in production: a name of "技術白皮書" (technical
# whitepaper) survived the whole-string check because it was not *longer* than
# that suffix, then the shorter "白皮書" suffix trimmed it down to a
# meaningless "技術" ("technical"), which shipped as a concept until caught.
DEFAULT_GENERIC_SUFFIXES = sorted(
    [
        "技術白皮書", "理論草稿", "概念論文", "白皮書", "論文", "總論", "系列索引",
        "Whitepaper", "White Paper", "Technical Report", "Overview", "Draft", "Notes",
    ],
    key=len,
    reverse=True,
)

MIN_NAME_LENGTH = 4  # below this, a trimmed/extracted name is noise, not a concept

_ROMAN_NUMERAL_RE = re.compile(r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def looks_like_roman_numeral(s: str) -> bool:
    """Series numbering ("Method III", "Part IV") matches [A-Z]{2,8} exactly as
    well as a real acronym does. Without this filter, "III" becomes a concept
    whose "expansion" is whatever words happened to follow it."""
    return bool(s) and bool(_ROMAN_NUMERAL_RE.match(s))


def clean_name(name: str, generic_suffixes=None) -> str | None:
    """Trim boilerplate off an extracted name; return None if nothing real is
    left underneath it."""
    suffixes = generic_suffixes if generic_suffixes is not None else DEFAULT_GENERIC_SUFFIXES
    name = name.strip()
    if name in suffixes:
        return None  # the "name" IS just boilerplate
    for suf in suffixes:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            name = name[: -len(suf)]
            break  # longest-first order: stop at the first (longest) match
    name = name.strip("_ ：:-–—")
    return name if len(name) >= MIN_NAME_LENGTH else None


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", s).strip("-").lower()
    return s or "concept"


def extract_acronym_pairs(documents, patterns=None, generic_suffixes=None):
    """Find (acronym, expansion) pairs that documents state in their own titles.

    `documents` is any iterable of dicts with "id" and "title".

    Returns {acronym: (name, source_id)} plus a separate set of acronyms that
    were seen with CONFLICTING expansions. A conflict is not resolved by
    first-wins or last-wins: if two documents disagree about what an acronym
    expands to, this module genuinely does not know which is right, and
    asserting either one would be a fabricated alias. Conflicts are dropped
    and reported so a human can decide.
    """
    patterns = patterns or DEFAULT_ACRONYM_PATTERNS
    found: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()

    for doc in documents:
        title = (doc.get("title") or "").strip()
        if not title:
            continue
        acro = name = None
        for order, rx in patterns:
            m = rx.match(title)
            if not m:
                continue
            if order == "acronym_first":
                acro, name = m.group(1), clean_name(m.group(2), generic_suffixes)
            else:
                name, acro = clean_name(m.group(1), generic_suffixes), m.group(2)
            break
        if not acro or not name or looks_like_roman_numeral(acro):
            continue
        if acro in found:
            if found[acro][0] != name:
                ambiguous.add(acro)
            continue
        found[acro] = (name, doc.get("id"))

    for acro in ambiguous:
        found.pop(acro, None)
    return found, ambiguous


def acronym_entries(documents, excluded=None, patterns=None, generic_suffixes=None) -> list:
    """Dictionary entries from self-defining titles. `excluded` is the set of
    acronyms you have reviewed and decided NOT to alias — see
    audit_candidates() for how to build it honestly."""
    excluded = set(excluded or ())
    pairs, _ambiguous = extract_acronym_pairs(documents, patterns, generic_suffixes)
    entries = []
    for acro, (name, source_id) in sorted(pairs.items()):
        if acro in excluded:
            continue
        entries.append({
            "concept_id": f"concept-{slugify(acro)}",
            "canonical": name,
            "aliases": [{"term": acro, "weight": 0.92}],
            "related": [],
            "broader": [],
            "narrower": [],
            "status": "system_inferred",
            "evidence": {"source_document_id": source_id, "method": "self_defining_title_pattern"},
        })
    return entries


def curated_entries(alias_sets) -> list:
    """Dictionary entries from alias sets you already curate elsewhere.

    Each item: {"id", "canonical", "aliases": [str | {"term", "weight"}]}.
    A set with no aliases beyond its canonical name is skipped — an entry that
    expands a term only to itself adds nothing but index weight.

    Tagged `system_inferred`, not `editor_confirmed`: reusing already-curated
    data programmatically is not the same act as a human reviewing THIS
    dictionary. If you have genuinely reviewed the generated file, set the
    status yourself downstream — don't let this module claim it for you.
    """
    entries = []
    for s in alias_sets or []:
        canonical = s.get("canonical") or s.get("title")
        if not canonical:
            continue
        aliases = []
        for a in s.get("aliases") or []:
            term = a if isinstance(a, str) else a.get("term")
            if not term or term == canonical:
                continue
            weight = 0.90 if isinstance(a, str) else float(a.get("weight", 0.90))
            aliases.append({"term": term, "weight": weight})
        if not aliases:
            continue
        entries.append({
            "concept_id": f"concept-{slugify(s.get('id') or canonical)}",
            "canonical": canonical,
            "aliases": aliases,
            "related": [{"term": t, "weight": 0.70} for t in (s.get("related") or [])],
            "broader": s.get("broader") or [],
            "narrower": s.get("narrower") or [],
            "status": "system_inferred",
            "evidence": {"source_record_id": s.get("id"), "method": "curated_alias_set"},
        })
    return entries


def audit_candidates(documents, excluded=None, patterns=None, generic_suffixes=None) -> dict:
    """Everything the extractor found, INCLUDING what it rejected and why.

    Read this before shipping a generated dictionary. Mechanical extraction on
    a real corpus reliably surfaces a handful of pairs that are technically
    well-formed but would still mislead — an acronym this corpus redefines
    that is famous for something else, a namespace collision with a different
    project, a subtitle mistaken for an expansion. Those belong in `excluded`,
    with a written reason, not silently trusted.
    """
    excluded = set(excluded or ())
    pairs, ambiguous = extract_acronym_pairs(documents, patterns, generic_suffixes)
    return {
        "accepted": [{"acronym": a, "canonical": n, "source_document_id": sid}
                     for a, (n, sid) in sorted(pairs.items()) if a not in excluded],
        "excluded_by_config": sorted(a for a in pairs if a in excluded),
        "dropped_ambiguous": sorted(ambiguous),
        "counts": {
            "accepted": sum(1 for a in pairs if a not in excluded),
            "excluded_by_config": sum(1 for a in pairs if a in excluded),
            "dropped_ambiguous": len(ambiguous),
        },
    }


def build_dictionary(documents=None, curated_alias_sets=None, excluded_acronyms=None,
                     patterns=None, generic_suffixes=None) -> list:
    """Full entry list. Curated sets come first so that, for an equal-scoring
    match, a term you explicitly maintain outranks a mechanically-extracted
    one."""
    return (
        curated_entries(curated_alias_sets)
        + acronym_entries(documents or [], excluded_acronyms, patterns, generic_suffixes)
    )


def compact_dictionary(entries: list) -> list:
    """Strip the entry down to what core/scoring.js's expandQuery() reads.
    Provenance (`status`, `evidence`) stays in the raw file for auditing but is
    not shipped to every visitor's browser."""
    return [
        {
            "concept_id": e["concept_id"],
            "canonical": e["canonical"],
            "aliases": e["aliases"],
            "related": e["related"],
        }
        for e in entries
    ]


def write_dictionary(entries: list, out_path, raw_path=None, build_id=None) -> dict:
    """Write the served (compact) dictionary, and optionally a full raw file
    with provenance intact for auditing."""
    compact = compact_dictionary(entries)
    payload = {
        "schema_version": "0.1",
        "generated_at": _now(),
        "build_id": build_id,
        "count": len(compact),
        "note": "DRVS concept dictionary. Every entry is system_inferred — either a "
                "curated alias set, or an acronym a document self-defines in its own "
                "title. No entry is a guessed or model-generated expansion.",
        "entries": compact,
    }
    out_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out_text, encoding="utf-8")

    if raw_path is not None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps({"generated_at": _now(), "count": len(entries), "entries": entries},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {"count": len(entries), "bytes": len(out_text.encode("utf-8"))}
