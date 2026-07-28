# DRVS — Dynamic Revealing Vector Search

A corpus-agnostic search engine that fuses five channels — exact, lexical
(typo/paraphrase-tolerant), dictionary (alias expansion), relation (curated
document-to-document links), and semantic (embedding vectors, doc-level and
chunk-level) — into one ranked, honestly-labeled result list, running mostly
client-side against a static index.

Distilled out of a real production deployment (a ~2,000-document technical
corpus, five build phases, and a handful of real bugs found by testing
against actual data rather than trusting the code) into a package meant to
be dropped onto a *different* corpus without dragging that corpus's own
concerns along with it.

## Why

Most "add search to my static site" options are either full-text search with
no way to express structure (relations, aliases, confidence), or a hosted
semantic-search API that costs money per query and needs a server. DRVS
takes a third path: a small, dependency-free scoring core that runs
identically in a Node test and a browser Worker, an optional in-browser
embedding model (no server, no per-query cost) for the semantic channel, and
a build-time Python pipeline for chunking and embedding — all consuming one
plain JSON interchange format so you can swap out literally everything about
where your documents come from without touching the ranking logic.

## Design principles

- **A hit reason must name what actually matched.** Never an opaque
  relevance number — every result carries a specific, honest label ("exact
  title match", "known alias", "this specific paragraph", "same series as a
  confirmed match") so a reader can tell why something showed up.
- **Zero-guess relations and aliases.** Every relation and every dictionary
  entry must trace back to real evidence — curated data, a self-defining
  naming convention, an explicit citation — never a plausible-sounding
  invention. An empty relation list is honest; a fabricated one is not.
- **Every optional channel degrades structurally, never catastrophically.**
  No dictionary → no expansion, silently. No vectors, or the model fails to
  load → exact/lexical/relation search keeps working, silently. Nothing
  upstream of a channel knows or cares whether it's present.
- **Never return zero results without saying so.** A query that doesn't
  match anything confidently still returns something, with an honest
  low-confidence disclosure — not a misleading "0 results" dead end, and not
  a fake confident-looking match either.

## Quick start

```bash
npm install   # (or just import core/scoring.js directly — zero runtime deps)
node examples/demo-corpus/query_demo.mjs
```

That runs real queries (exact match, dictionary alias, relation-based
discovery, an honestly-low-confidence cross-domain query) against a small
synthetic 9-document corpus about entirely mundane things — coffee, tomato
plants, a bike chain — proving the whole pipeline works on something that
has nothing to do with what this package was extracted from.

To build the demo corpus's index/vectors yourself (optional — the output is
already committed):

```bash
pip install sentence-transformers
python examples/demo-corpus/build_demo.py
```

## Package layout

```
core/        pure JS scoring engine — no DOM/Worker/fetch, runs in Node or a browser Worker identically
client/      browser-side semantic channel — in-browser embedding model + vector fetch/compare
build/       Python build-time pipeline — chunking, embedding (two-tier incremental storage), relation merging
schema/      the Document / compact-record / dictionary / relation interchange format
examples/    a complete, working, tiny reference integration
docs/        step-by-step guide for adapting your own corpus
tests/       real tests against real (if tiny) data, not just synthetic sanity checks
```

Start with [`schema/document.md`](schema/document.md) to understand the data
shapes, then [`docs/ADAPTING_A_CORPUS.md`](docs/ADAPTING_A_CORPUS.md) for the
integration walkthrough.

## What's genuinely generic vs. what you still have to build

**Generic, ships as-is:** the scoring/ranking/diversity-reranking core, the
browser-side vector client, the chunking algorithm, the two-tier
incremental-embedding pipeline, the relation-merge-by-priority utility.

**Yours to build:** the adapter that turns your actual source files into
Documents. DRVS deliberately has no opinion on what a "corpus" is made of —
that's the one part no generic package can do for you honestly.

## Status

v0.1 — the scoring/client/build modules are the same logic (not a rewrite)
that ran against a real ~2,000-document corpus across five build phases, now
decoupled from that corpus's specific concerns and re-verified against an
unrelated synthetic corpus. Not yet published to npm/PyPI; import directly
from this repo.

## License

MIT — see [LICENSE](LICENSE).
