# drvs.evemisslab.com

The package's own site. Its demo runs the real `core/`, `client/`, and `ui/`
files — `build.py` copies them out of the package rather than keeping
site-local copies, so the thing being demonstrated cannot quietly drift from
the thing being shipped.

```bash
python3 examples/demo-corpus/build_demo.py   # both corpus editions (needs a model)
python3 site/build.py                        # assemble site/dist/
npx wrangler deploy                          # publish
```

`site/dist/` is generated and git-ignored. `site/src/` holds the templates,
`site/i18n/` holds every string on the site.

## Bilingual

| | English | 繁體中文 |
|---|---|---|
| route | `/` | `/zh/` |
| copy | `i18n/en.json` | `i18n/zh-Hant.json` |
| corpus | `examples/demo-corpus/docs-en/` | `examples/demo-corpus/docs/` |
| documents | `/demo/{id}/` | `/zh/demo/{id}/` |
| embedding model | `bge-small-en-v1.5` (384d) | `bge-small-zh-v1.5` (512d) |

Each locale gets its own corpus, index, dictionary, reason labels, embedding
model, and document pages. An English page never shows a Chinese corpus, which
is the reason it is built this way rather than translating the chrome and
leaving the content alone — a demo whose examples the reader cannot read is
not a demo.

The two corpus editions are deliberately **parallel**: same ids, same
relations, same structure, so the demo behaves identically in both and any
difference is a real property of the engine rather than of the content. That
is also how "not script-specific" gets checked rather than asserted — the
lexical channel takes completely different paths for the two (character
bigrams vs. whitespace tokens).

Adding a third locale means: a `docs-xx/` corpus, an entry in `adapter.py`'s
`LOCALES`, a `site/i18n/xx.json`, and label/relation entries in
`site/build.py`. No template changes. (`build.py` currently asserts exactly two
bundles — relax that check when a third arrives.)

## Notes

Document rows are rendered into the HTML at build time from the corpus index,
not injected by script. That is deliberate: the package's central claim is
that the reveal layer is *additive*, and a demo whose content only appears
once JavaScript runs would be quietly contradicting it. Turn JavaScript off
and all nine documents are still there.

Design direction is a darkroom. 顯影 — the original name for this technique —
is the word for photographic development, an image brought up out of paper it
was already on. Hence safelight amber on darkroom dark, and a step wedge (the
calibrated density strip used to check an exposure) as the figure for tiered
confidence. The wedge's blocks carry the literal tier opacities from
`config/search.config.json`.

If you are verifying locally and see stale strings or the wrong corpus, it is
the dev server's cache, not the build: ES modules and JSON are cached hard by
`http.server`, which sends no revalidation headers. Serve on a fresh port.
Cloudflare sends `max-age=0, must-revalidate`, so production does not have
this problem.
