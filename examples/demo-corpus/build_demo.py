#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS demo-corpus build script.

Runs the full pipeline end to end on the demo corpus: adapter -> Document
list -> compact search index -> chunking -> doc-level + chunk-level
embeddings. Same pipeline shape a real integration follows (see
docs/ADAPTING_A_CORPUS.md), at a scale that runs in seconds and whose output
is small enough to commit.

The corpus exists in two parallel languages — same ids, same relations, same
structure — and each is built independently, including with its OWN embedding
model. Running one language's model over the other would quietly produce a
worse-but-not-obviously-broken semantic channel, which is exactly the class of
silent degradation this package tries not to have.

Usage:
    python examples/demo-corpus/build_demo.py [--force] [--locale zh-Hant|en|all] [--no-vectors]

Requires `pip install sentence-transformers` — the one step that needs a real
model, kept out of the base package's required dependencies. `--no-vectors`
builds just the index and dictionary, which needs no model at all.
"""
import json
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).parent
REPO_ROOT = DEMO_DIR.parent.parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DEMO_DIR))

from adapter import load_documents, locale_config, LOCALES  # noqa: E402
from build.chunk import chunk_document  # noqa: E402
from build.embed import embed_documents, embed_chunks, default_doc_text  # noqa: E402

BUILD_ID = "demo"


def compact(doc: dict) -> dict:
    """Long-form Document -> the short-key wire format core/scoring.js
    reads. See schema/document.md for the field meanings."""
    return {
        "i": doc["id"], "t": doc["title"], "u": doc["url"], "d": doc.get("date", ""),
        "s": doc.get("summary", ""), "h": doc.get("headings", []), "k": doc.get("keywords", []),
        "r": [[rel["id"], rel["type"]] for rel in doc.get("related_ids", [])],
        "p": (doc.get("series") or [None])[0],
    }


def build_locale(locale: str, force: bool = False, skip_vectors: bool = False) -> dict:
    cfg = locale_config(locale)
    generated_dir = DEMO_DIR / "generated" / locale
    dist_dir = DEMO_DIR / "dist" / locale
    generated_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    docs = load_documents(locale)
    order = [d["id"] for d in docs]

    (generated_dir / "documents.raw.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n",
        encoding="utf-8",
    )

    index_payload = {
        "schema_version": "0.1",
        "build_id": BUILD_ID,
        "locale": locale,
        "count": len(docs),
        "documents": [compact(d) for d in docs],
    }
    (dist_dir / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8",
    )
    (dist_dir / "dictionary.json").write_text(
        json.dumps({"entries": cfg["dictionary"]}, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    if skip_vectors:
        return {"locale": locale, "documents": len(docs), "vectors": "skipped"}

    embed_kwargs = {
        "model_name": cfg["model"],
        "onnx_model_name": cfg["onnx_model"],
        "dim": cfg["dim"],
        "query_instruction": cfg["query_instruction"],
        "build_id": BUILD_ID,
        "force": force,
    }

    doc_stats = embed_documents(
        items=[{"id": d["id"], "text": default_doc_text(d)} for d in docs],
        order=order,
        manifest_path=generated_dir / "embeddings-manifest.json",
        binary_path=generated_dir / "embeddings.f32.bin",
        dist_binary_path=dist_dir / "vectors.bin",
        dist_meta_path=dist_dir / "vectors-meta.json",
        **embed_kwargs,
    )

    all_chunks = []
    for d in docs:
        for c in chunk_document(d["id"], d["title"], d["body_text"]):
            c["doc_id"] = d["id"]
            all_chunks.append(c)
    chunk_stats = embed_chunks(
        chunks=all_chunks,
        manifest_path=generated_dir / "chunk-embeddings-manifest.json",
        binary_path=generated_dir / "chunk-embeddings.f32.bin",
        dist_binary_path=dist_dir / "chunks.bin",
        dist_meta_path=dist_dir / "chunks-meta.json",
        **embed_kwargs,
    )

    return {"locale": locale, "documents": doc_stats, "chunks": chunk_stats}


def main():
    force = "--force" in sys.argv
    skip_vectors = "--no-vectors" in sys.argv

    locale_arg = "all"
    if "--locale" in sys.argv:
        locale_arg = sys.argv[sys.argv.index("--locale") + 1]
    locales = list(LOCALES) if locale_arg == "all" else [locale_arg]

    results = [build_locale(loc, force, skip_vectors) for loc in locales]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
