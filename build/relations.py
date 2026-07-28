#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS — Dynamic Revealing Vector Search
build/relations.py: merge multiple relation sources into one map, with a
priority order that survives call-order changes.

Real corpora often have MORE THAN ONE way to derive a relation between two
documents -- e.g. "these two are consecutive versions of the same work" AND
"these two happen to share a keyword" can both be true of the same pair. When
that happens, only the MOST SPECIFIC relation should be kept (a reader wants
"this is the sequel" over "these share a keyword" when both are true) — see
DEFAULT priority reasoning below for why this matters more than it looks.

This module is deliberately the ONLY piece of the "how do I find relations
between my documents" problem that lives in DRVS's generic core — everything
about WHERE relations come from (citation graphs, version histories, curated
series membership, shared tags, ...) is corpus-specific and belongs in your
own adapter. What's generic and worth reusing is the MERGE step once you have
several candidate maps.
"""


def merge_relations(*relation_maps, priority):
    """Combine several `doc_id -> [(target_id, type_code), ...]` maps, keeping
    at most one entry per (doc_id, target_id) pair.

    priority: list of type codes, MOST SPECIFIC FIRST. When the same
    (doc_id, target_id) pair arises from more than one source map with
    different type codes, the one earliest in `priority` wins. A type code
    not present in `priority` is treated as lowest priority (never crashes on
    an unrecognized code — it just loses every tie).

    The result is independent of what order you pass the maps in — this is
    the whole point. A prior version of this exact function (in the project
    this package was distilled from) picked the winner by "first map passed
    wins" insertion order instead of by `priority`, which silently made the
    priority list dead code: a broad "same series" relation was always
    passed first and so always won, even though a more specific
    "previous_version"/"next_version" relation existed for the very same
    pair — because EVERY version-adjacent pair was, by construction, ALSO a
    same-series pair (both were derived from the same underlying grouping
    data). The result: the specific relation type had a 0% survival rate
    against the general one, for every single pair, silently -- caught only
    by inspecting real build output (zero entries of that type anywhere in
    the corpus), not by a synthetic unit test with made-up data that
    couldn't reproduce the type-collision scenario in the first place. Fixed
    by collecting every CANDIDATE type per (doc_id, target_id) pair across
    ALL sources first, then picking the winner by `priority` — never by
    argument order. If you use this function for your own corpus, keep a
    regression test that builds a real type-collision case the same way
    (see tests/test_relations.py) — a synthetic test with independent,
    non-colliding sample data will not catch this class of bug.
    """
    candidates = {}
    for rel_map in relation_maps:
        for doc_id, pairs in rel_map.items():
            bucket = candidates.setdefault(doc_id, {})
            for target, rtype in pairs:
                if target == doc_id:
                    continue
                bucket.setdefault(target, []).append(rtype)

    def rank(rtype):
        return priority.index(rtype) if rtype in priority else len(priority)

    return {
        doc_id: sorted(
            ((target, min(types, key=rank)) for target, types in bucket.items()),
            key=lambda kv: (rank(kv[1]), kv[0]),
        )
        for doc_id, bucket in candidates.items()
    }
