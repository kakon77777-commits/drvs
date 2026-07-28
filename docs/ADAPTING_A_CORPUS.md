# Adapting a corpus to DRVS

This walks through everything a new corpus integration needs, in the order
you'll actually do it. The demo at `examples/demo-corpus/` is a complete,
working, minimal example of every step below — read alongside it.

## 1. Write an adapter

DRVS has no idea what your source files look like — markdown with YAML
frontmatter, a database table, a CMS export, whatever. Your adapter's only
job is producing a list of [Document](../schema/document.md#1-document-long-form-build-time)
records from whatever you actually have.

Start minimal: `id`, `title`, `url` are the only required fields. Everything
else (`summary`, `headings`, `keywords`, ...) directly improves search
quality but nothing crashes without it — a document with no summary just has
weaker exact/lexical signal, not an error.

See `examples/demo-corpus/adapter.py` for a complete (if deliberately
simple) reference.

## 2. Decide on relations (optional, and zero-guess)

If your corpus has genuine structural relationships between documents —
version history, curated series, citation links, shared tags — encode them
as `related_ids: [{id, type}]` on each Document. **Never invent a relation
you can't point to real evidence for.** An empty `related_ids` list is
honest; a plausible-sounding guessed one is not, and it's shown to the user
as if it were fact.

If you have more than one source of relations that might disagree about the
same pair (this is common — e.g. "same series" and "consecutive versions"
usually overlap almost entirely), route them through
`build/relations.py`'s `merge_relations()` rather than picking a winner
yourself. Read that function's docstring before skipping this step — it
documents a real bug where skipping it caused one specific, more-useful
relation type to have a 0% survival rate against a broader one, silently,
for months.

**Verify direction on one concrete example before trusting it corpus-wide.**
See `schema/document.md` §3 — a reversed previous/next relation doesn't
crash anything, it just quietly shows the wrong label forever.

## 3. Build the compact search index

Convert your Document list to the [compact wire format](../schema/document.md#2-compact-record-search-time-wire-format)
and serve it as static JSON (or return it from an API — DRVS's client side
just needs `fetch()`-able JSON, it doesn't care how). This alone — no
dictionary, no vectors — gives you exact + lexical + relation search.

```js
import { scoreCorpus, prepareIndex, DEFAULT_CONFIG } from "drvs/core";

const documents = prepareIndex(await fetch("/index.json").then(r => r.json()).then(r => r.documents));
const result = scoreCorpus(documents, userQuery, DEFAULT_CONFIG, /* dictionary */ []);
```

## 4. Optional: a concept dictionary for alias expansion

If your corpus has known aliases/abbreviations/alternate names for the same
concept, build a [Dictionary](../schema/document.md#4-dictionary-entry-optional--for-query-expansion)
and pass it as `scoreCorpus`'s 4th argument. Same zero-guess rule as
relations: only encode aliases you have real evidence for (a naming
convention your own titles already follow, curated metadata you already
maintain) — never ask a model to free-associate synonyms for you and ship
the result as if it were fact.

**Watch for a keyword shadowing its own alias entry.** If a document already
lists the alias term as a literal `keywords` entry, the exact-match channel
will usually outscore the alias-expansion channel for that exact query,
making your dictionary entry look like it's not doing anything (it isn't,
for that specific query — the exact channel already won). This isn't a bug,
but it can look like one while you're testing; check which channel actually
fired (`result.results[0].channels`) before concluding expansion is broken.

## 5. Optional: chunk-level ("which passage matched") search

If your documents are long enough that "the whole document is about X" isn't
precise enough, add per-section chunking:

```python
from build.chunk import chunk_document

chunks = []
for doc in documents:
    for c in chunk_document(doc["id"], doc["title"], doc["body_text"]):
        c["doc_id"] = doc["id"]
        chunks.append(c)
```

`chunk_document`'s heading detection defaults to markdown `##`/`###`; pass
your own `heading_re` for a different convention (see that function's
docstring — the heading TEXT must be the LAST capture group).

## 6. Optional: embeddings (semantic search)

Requires `pip install sentence-transformers` (or your own encoder — see
`build/embed.py`'s `encoder` parameter, used by this package's own tests to
avoid downloading a real model just to test the storage/incremental logic).

```python
from build.embed import embed_documents, embed_chunks, default_doc_text

embed_documents(
    items=[{"id": d["id"], "text": default_doc_text(d)} for d in documents],
    order=[d["id"] for d in documents],
    manifest_path=..., binary_path=...,       # persisted, commit to version control
    dist_binary_path=..., dist_meta_path=...,  # regenerated every build, served to the browser
)
```

Then in the browser:

```js
import { createVectorClient } from "drvs/client";

const vectorClient = createVectorClient({
  vectorsUrl: "/vectors.bin",
  vectorsMetaUrl: "/vectors-meta.json",
  // chunksUrl / chunksMetaUrl: optional, if you built chunk embeddings too
});
vectorClient.warmUp(); // call once at page/worker init, never blocks

// per search:
const semanticScores = await vectorClient.semanticScores(userQuery, idToIndexMap);
const result = scoreCorpus(documents, userQuery, config, dictionary, semanticScores);
```

**You almost certainly need to recalibrate `rawFloor`/`rawCeiling`** if
you're using a different embedding model, or a corpus whose topical spread
differs a lot from a ~2000-document Chinese technical-writing corpus (the
defaults' origin). Recipe: run a spread of queries against your own built
vectors — a few "clearly on-topic for a specific document", a few "clearly
unrelated to anything in the corpus" — and look at the raw cosine values
before any rescaling. Set `rawFloor` just above where the unrelated queries'
best score tops out, and `rawCeiling` around where your strongest on-topic
matches land. See `client/vector-client.js`'s module header for the full
reasoning and the numbers this package's own reference deployment observed.

## 7. Tune the config

`core.DEFAULT_CONFIG` (weights, tiers, diversity quotas, expansion limits)
are reasonable starting points, not universal constants. If your corpus is
much smaller or much larger, if your documents are much longer or shorter,
or if a particular channel matters more or less for your use case, override
the relevant sub-object — see `core/scoring.js`'s `DEFAULT_*` exports for
what's tunable and why each default is what it is.

## 8. Test your integration

Don't just eyeball a few queries in a browser. Build a small test set
covering: exact title matches, known aliases, natural-language paraphrases
with little lexical overlap, typos, queries that should honestly return
low-confidence (nothing in your corpus is actually relevant), and — if
applicable — queries that should surface a companion document via a
relation, not a direct hit. `examples/demo-corpus/query_demo.mjs` and
`tests/test_scoring.mjs` are complete, runnable examples of this pattern
against real (if tiny) data — copy the shape, not the specific queries.

## Common pitfalls

- **A dictionary alias shadowed by a literal keyword** — see step 4 above.
- **Reversed relation direction** — see step 2 above and `schema/document.md` §3.
- **Custom `heading_re` with the heading text in the wrong capture group** —
  must be the LAST group, not necessarily group 1. See `build/chunk.py`.
- **Shipping the default `rawFloor`/`rawCeiling` unchanged for a very
  different corpus or model** — silently mis-ranks the semantic channel
  without any error to catch it. See step 6 above.
- **Treating a "same map order" test as proof `merge_relations` works** — a
  synthetic test with independent, non-colliding sample data cannot catch
  the priority-vs-argument-order bug class; you need a real collision case
  (`tests/test_relations.py` has one you can copy the shape of).
