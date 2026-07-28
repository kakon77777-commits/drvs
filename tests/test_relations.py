#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS test suite — build/relations.py's merge_relations().

The most important case here is a REAL type-collision scenario -- the same
(doc, target) pair produced by two DIFFERENT relation sources with two
DIFFERENT type codes -- not independent synthetic data. A prior version of
this exact function (see the module's own docstring) picked the winner by
"whichever map was passed first" instead of by priority, and every
synthetic test using independent, non-colliding sample data passed anyway,
because none of those tests constructed a pair two sources could disagree
about. That bug was only caught by inspecting real build output. This suite
exists specifically so it can never regress silently again.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from build.relations import merge_relations

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


# --- basic single-source passthrough -----------------------------------------
single = {"a": [("b", "s")], "b": [("a", "s")]}
result = merge_relations(single, priority=["s"])
check("single source passes through unchanged", result == {"a": [("b", "s")], "b": [("a", "s")]}, f"got={result}")

# --- self-relation filtering --------------------------------------------------
with_self = {"a": [("a", "s"), ("b", "s")]}
result = merge_relations(with_self, priority=["s"])
check("a self-referencing pair (target == doc_id) is always dropped", result["a"] == [("b", "s")], f"got={result}")

# --- THE regression case: real type collision, priority order must win, -----
# NOT argument order. This mirrors the exact production bug: every
# "next_version"/"previous_version" pair is ALSO a "same_series" pair
# because both are derived from the same underlying grouping -- so if
# argument order (not priority) ever decides the winner again, this fails.
series_map = {"a": [("b", "s")], "b": [("a", "s")]}
version_map = {"a": [("b", "n")], "b": [("a", "p")]}
priority = ["p", "n", "s"]  # most specific first

merged_series_first = merge_relations(series_map, version_map, priority=priority)
merged_version_first = merge_relations(version_map, series_map, priority=priority)

check(
    "specific relation type (n) wins over general (s) for the same pair",
    merged_series_first["a"] == [("b", "n")],
    f"got={merged_series_first['a']}",
)
check(
    "specific relation type (p) wins over general (s) for the same pair",
    merged_series_first["b"] == [("a", "p")],
    f"got={merged_series_first['b']}",
)
check(
    "result is IDENTICAL regardless of which map is passed first -- "
    "this is the exact case the production bug got backwards",
    merged_series_first == merged_version_first,
    f"series_first={merged_series_first} version_first={merged_version_first}",
)

# --- three-way collision: priority order fully decides, not insertion ------
map_low = {"a": [("b", "k")]}
map_mid = {"a": [("b", "s")]}
map_high = {"a": [("b", "e")]}
priority3 = ["e", "s", "k"]
for combo in [
    (map_low, map_mid, map_high),
    (map_high, map_mid, map_low),
    (map_mid, map_high, map_low),
]:
    r = merge_relations(*combo, priority=priority3)
    check(
        f"3-way collision picks the highest-priority type regardless of arg order {[id(m) for m in combo]}",
        r["a"] == [("b", "e")],
        f"got={r['a']}",
    )

# --- unrecognized type code never crashes, falls to lowest priority --------
unknown = {"a": [("b", "totally_unknown_code")]}
known = {"a": [("b", "s")]}
result = merge_relations(unknown, known, priority=["s"])
check(
    "an unrecognized type code loses to any code that IS in the priority list",
    result["a"] == [("b", "s")],
    f"got={result['a']}",
)
only_unknown = merge_relations(unknown, priority=["s"])
check(
    "an unrecognized type code with no competing source never crashes",
    only_unknown["a"] == [("b", "totally_unknown_code")],
    f"got={only_unknown['a']}",
)

# --- deterministic ordering across multiple targets --------------------------
multi = {"a": [("z", "s"), ("b", "s"), ("m", "s")]}
result = merge_relations(multi, priority=["s"])
check(
    "same-priority targets sort by target id for deterministic output",
    result["a"] == [("b", "s"), ("m", "s"), ("z", "s")],
    f"got={result['a']}",
)

# --- empty inputs never crash -------------------------------------------------
check("no source maps returns an empty result", merge_relations(priority=["s"]) == {})
check("a source map with an empty pair list is fine", merge_relations({"a": []}, priority=["s"]) == {"a": []})

print(f"\n--- {pass_count}/{pass_count + fail_count} passed ---")
sys.exit(0 if fail_count == 0 else 1)
