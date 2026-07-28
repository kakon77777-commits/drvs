# Document schema

DRVS moves data between three stages, each with its own shape. This is the
contract every corpus adapter must produce, and every core/client module
consumes.

## 1. Document (long-form, build-time)

What your corpus adapter extracts from your actual source files. Plain
dict/object — no required class, just these keys:

| field         | type              | required | notes |
|---------------|-------------------|----------|-------|
| `id`          | string            | yes      | stable, unique across the corpus — used as the join key everywhere (vectors, relations, chunks) |
| `title`       | string            | yes      | |
| `url`         | string            | yes      | wherever the document lives (a path, a full URL — whatever your client-side code links to) |
| `date`        | string            | no       | free-form; only used for display, never parsed by core |
| `language`    | string            | no       | free-form (e.g. `"zh-Hant"`, `"en"`) |
| `summary`     | string            | no       | short prose summary — the single highest-value field for both search quality and doc-level embedding input |
| `headings`    | list[string]      | no       | section headings, in document order — feeds exact-match search AND the default embedding text (see `build/embed.py`'s `default_doc_text()`) |
| `body_text`   | string            | no       | full plain body text, frontmatter/markup already stripped — only needed if you're using `build/chunk.py` for chunk-level vectors; not embedded directly at doc level |
| `keywords`    | list[string]      | no       | short tag-like terms; exact keyword match is a real (Tier A) signal, so keep this list genuinely representative, not padded |
| `series`      | list[string]      | no       | human-readable series/group label(s) this document belongs to — `series[0]` becomes the compact record's `p` field, used by diversity reranking's per-series quota |
| `related_ids` | list[{id, type}]  | no       | `type` is a short code you define (see §3 below) — never guess a relation your adapter can't point to real evidence for; an empty list is honest, a fabricated one is not |

## 2. Compact record (search-time wire format)

What `core/scoring.js` actually reads, and what you should serve to the
browser (short keys keep the served index small — this matters at a few
thousand documents). Build this from your Document list right before you
write your search index:

```js
function compact(doc) {
  return {
    i: doc.id,
    t: doc.title,
    u: doc.url,
    d: doc.date || "",
    s: doc.summary || "",
    h: doc.headings || [],
    k: doc.keywords || [],
    r: (doc.related_ids || []).map((rel) => [rel.id, rel.type]),
    p: (doc.series && doc.series[0]) || null,
  };
}
```

`documents[i]`'s array position (0-based) is also the index every vector
file and `semanticScores` Map uses to refer to that document — see
`build/embed.py`'s `order` parameter and `client/vector-client.js`'s
`idToIndex` parameter. Keep one single canonical array order and reuse it
everywhere; a mismatched order silently attaches the wrong vector to the
wrong document, with no error to catch it.

## 3. Relation type codes

`r` is a list of `[target_id, type_code]` pairs. `type_code` is any short
string you define — DRVS ships a default vocabulary
(`core.DEFAULT_RELATION_TYPES` / `core.scoring.DEFAULT_RELATION_TYPES`) with
five common types (`s` same-series, `p` previous-version, `n` next-version,
`e` explicit-link, `k` same-primary-keyword), each with a display label and
which weight bucket it draws from — but you can pass your own
`cfg.relationTypes` with entirely different codes. An unrecognized code never
crashes; it falls back to a generic "related" label rather than silently
mislabeling the relation as something more specific than it is.

**Direction matters and is easy to get backwards.** A pair `[targetId, "p"]`
stored on `docA.related_ids` means *targetId is docA's own previous version*
— i.e. **docA is the later one**. When you build `related_ids`, verify the
direction against one concrete real example from your own corpus before
trusting it corpus-wide — a reversed previous/next relation doesn't crash
anything, it just quietly shows the wrong label forever. This bit a real
deployment (see `build/relations.py`'s docstring for the exact bug) and is
the single easiest mistake to make when writing a new adapter.

If more than one of your relation sources can produce a candidate for the
same `(doc_id, target_id)` pair, use `build/relations.py`'s
`merge_relations()` to pick a winner by priority — see that module's
docstring for why "just keep whichever source ran first" is a bug waiting to
happen, not a simplification.

## 4. Dictionary entry (optional — for query expansion)

Only needed if you want `dictionary`-channel alias/related-term expansion
(`core.expandQuery` / the 4th argument to `core.scoreCorpus`). If you have no
dictionary, pass `[]` or `null` — the channel silently no-ops.

```json
{
  "concept_id": "concept-example",
  "canonical": "Full Concept Name",
  "aliases": [{ "term": "Short Name", "weight": 0.9 }],
  "related": [{ "term": "Adjacent Concept", "weight": 0.7 }]
}
```

`weight` is 0..1 confidence — entries below `expansion_limits.min_expansion_confidence`
(default 0.60) are never used. **Never assert an alias/related-term pair you
can't point to real evidence for** — a dictionary entry is a claim shown to
the user as if it were fact ("known alias") — see
`docs/ADAPTING_A_CORPUS.md` for zero-guess extraction strategies (structured
metadata your corpus already has, self-defining naming conventions in your
own titles) versus what NOT to do (asking a model to free-associate synonyms).

## 5. Chunk record (optional — for chunk-level / passage vectors)

`build/chunk.py`'s `chunk_document()` output, per document:

```json
{ "chunk_id": "doc-id#chunk-0", "heading": "3.2 Some Section", "text": "..." }
```

Add a `"doc_id"` key (the parent document's id) before passing a full
corpus's worth of these into `build/embed.py`'s `embed_chunks()` — see that
function's docstring.
