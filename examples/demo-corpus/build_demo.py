#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS demo-corpus build script.

Runs the full pipeline end to end on the 8-document demo corpus in docs/:
adapter -> Document list -> compact search index -> chunking -> doc-level +
chunk-level embeddings. This is the same pipeline shape a real corpus
integration follows (see docs/ADAPTING_A_CORPUS.md) at a scale small enough
to run in seconds and to commit its output to version control.

Usage:
    python examples/demo-corpus/build_demo.py [--force]

Requires `pip install sentence-transformers` (or any optional-dependency
extra that provides it) — this is the one step that needs a real model, so
it's kept out of the base package's required dependencies.
"""
import json
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).parent
REPO_ROOT = DEMO_DIR.parent.parent
GENERATED_DIR = DEMO_DIR / "generated"
DIST_DIR = DEMO_DIR / "dist"

sys.path.insert(0, str(REPO_ROOT))

from adapter import load_documents, DICTIONARY  # noqa: E402
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


def main():
    force = "--force" in sys.argv
    GENERATED_DIR.mkdir(exist_ok=True)
    DIST_DIR.mkdir(exist_ok=True)

    docs = load_documents()
    order = [d["id"] for d in docs]

    (GENERATED_DIR / "documents.raw.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in docs) + "\n",
        encoding="utf-8",
    )

    index_payload = {
        "schema_version": "0.1",
        "build_id": BUILD_ID,
        "count": len(docs),
        "documents": [compact(d) for d in docs],
    }
    (DIST_DIR / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8",
    )
    (DIST_DIR / "dictionary.json").write_text(
        json.dumps({"entries": DICTIONARY}, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    doc_items = [{"id": d["id"], "text": default_doc_text(d)} for d in docs]
    doc_stats = embed_documents(
        items=doc_items,
        order=order,
        manifest_path=GENERATED_DIR / "embeddings-manifest.json",
        binary_path=GENERATED_DIR / "embeddings.f32.bin",
        dist_binary_path=DIST_DIR / "vectors.bin",
        dist_meta_path=DIST_DIR / "vectors-meta.json",
        build_id=BUILD_ID,
        force=force,
    )

    all_chunks = []
    for d in docs:
        for c in chunk_document(d["id"], d["title"], d["body_text"]):
            c["doc_id"] = d["id"]
            all_chunks.append(c)
    chunk_stats = embed_chunks(
        chunks=all_chunks,
        manifest_path=GENERATED_DIR / "chunk-embeddings-manifest.json",
        binary_path=GENERATED_DIR / "chunk-embeddings.f32.bin",
        dist_binary_path=DIST_DIR / "chunks.bin",
        dist_meta_path=DIST_DIR / "chunks-meta.json",
        build_id=BUILD_ID,
        force=force,
    )

    print(json.dumps({"documents": doc_stats, "chunks": chunk_stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
