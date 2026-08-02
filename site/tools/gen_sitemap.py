# -*- coding: utf-8 -*-
"""Write sitemap.xml from what a build actually produced.

    python tools/gen_sitemap.py --root <built-dir> --origin https://example.com

Walks the built output for index.html files and writes one <url> per page. It
reads the pages rather than a list, so it cannot drift from the site: delete a
page and it leaves the sitemap on the next build.

Pages that carry <meta name="robots" content="noindex"> are skipped — a page
that tells crawlers to ignore it has no business being advertised in a sitemap.
A 404 page is the usual case.

Language variants under a two-letter prefix (/zh/foo) are emitted as hreflang
alternates of the bare URL rather than as separate <url> entries, which is what
a bilingual build of this shape means.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NOINDEX = re.compile(
    r'<meta[^>]+name=["\']robots["\'][^>]*content=["\'][^"\']*noindex', re.I)
LANG_DIR = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


def page_urls(root: Path):
    """(url_path, is_lang_variant, lang) for every indexable page."""
    for html in sorted(root.rglob("index.html")):
        rel = html.relative_to(root).parent
        parts = rel.parts
        if any(p.startswith(("_", ".")) for p in parts):
            continue
        if NOINDEX.search(html.read_text(encoding="utf-8", errors="replace")):
            continue
        if parts and LANG_DIR.match(parts[0]):
            yield "/" + "/".join(parts[1:]), True, parts[0]
        else:
            yield "/" + "/".join(parts), False, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--origin", required=True)
    ap.add_argument("--out")
    ap.add_argument("--default-lang", default="en")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    origin = args.origin.rstrip("/")

    canonical, langs = [], {}
    for path, is_variant, lang in page_urls(root):
        clean = "/" if path == "/" else path.rstrip("/") + "/"
        if is_variant:
            langs.setdefault(clean, set()).add(lang)
        elif clean not in canonical:
            canonical.append(clean)

    if not canonical:
        raise SystemExit(
            f"found no indexable pages under {root}; refusing to write an empty "
            "sitemap (an empty sitemap and a broken walk look identical)")

    urls = []
    for path in canonical:
        alts = ""
        if path in langs:
            pairs = [(args.default_lang, "")] + [
                (lang, f"/{lang}") for lang in sorted(langs[path])]
            alts = "".join(
                f'<xhtml:link rel="alternate"'
                f' hreflang="{"zh-Hant" if lang == "zh" else lang}"'
                f' href="{origin}{prefix}{path}"/>'
                for lang, prefix in pairs)
            alts += ('<xhtml:link rel="alternate" hreflang="x-default"'
                     f' href="{origin}{path}"/>')
        urls.append(f"<url><loc>{origin}{path}</loc>{alts}</url>")

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
           + "".join(urls) + "</urlset>\n")

    out = Path(args.out) if args.out else root / "sitemap.xml"
    out.write_text(xml, encoding="utf-8")
    skipped = sum(
        1 for f in root.rglob("index.html")
        if NOINDEX.search(f.read_text(encoding="utf-8", errors="replace")))
    print(f"{out}: {len(canonical)} urls"
          f"{f', {len(langs)} with language alternates' if langs else ''}"
          f"{f', {skipped} skipped (noindex)' if skipped else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
