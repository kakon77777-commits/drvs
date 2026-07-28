# drvs.evemisslab.com

The package's own site. Its demo runs the real `core/`, `client/`, and `ui/`
files — `build.py` copies them out of the package rather than keeping site-local
copies, so the thing being demonstrated cannot quietly drift from the thing
being shipped.

```bash
python3 site/build.py     # assemble site/dist/
npx wrangler deploy       # publish to drvs.evemisslab.com
```

`site/dist/` is generated and git-ignored. `site/src/` is the source.

The document rows are rendered into the HTML at build time from the demo
corpus index, not injected by script. That is deliberate: the package's central
claim is that the reveal layer is *additive*, and a demo whose content only
exists once JavaScript runs would be quietly contradicting it. Turn JavaScript
off and all nine documents are still there.

Design note: the visual direction is a darkroom. 顯影 — the original name for
this technique — is the word for photographic development, an image brought up
out of paper it was already on. Hence safelight amber on darkroom dark, and a
step wedge (the calibrated density strip used to check an exposure) as the
figure for tiered confidence. The wedge's blocks carry the literal tier
opacities from `config/search.config.json`.
