#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRVS — Dynamic Revealing Vector Search
build/embed.py: generic two-tier embedding pipeline for both document-level
and chunk-level vectors.

This module has no idea what a "document" or "corpus registry" is — it
operates on plain {id, text} records. embed_documents() and embed_chunks()
are two thin, differently-shaped callers of the same incremental-embed-and-
pack core (_embed_incremental), differing only in what extra metadata rides
along per record (chunk records also carry doc_id/heading for aggregating
chunk hits back to their parent document on the client).

Two-tier storage, for BOTH doc-level and chunk-level vectors:
    manifest_path / binary_path        PERSISTED, meant to be committed to
                                        version control: {id -> {hash, index}}
                                        manifest + packed float32 binary, in
                                        canonical sorted-id order. Re-running
                                        a build only re-embeds ids whose
                                        content hash changed since last time
                                        — the expensive part (a model call)
                                        never repeats work for free.
    dist_binary_path / dist_meta_path  DEPLOY ASSET, meant to be regenerated
                                        every build (cheap: no model call
                                        unless the manifest actually changed)
                                        — reordered to match whatever order
                                        the caller's client-side code expects
                                        (see `order` param on
                                        embed_documents()), so the browser
                                        needs no separate id->offset lookup.

A record with no embeddable text gets skipped (documents) or excluded
entirely (chunks) rather than crashing the whole batch — a build pipeline
processing an entire corpus must survive one malformed/empty record.
"""
import hashlib
import json
import struct
from datetime import datetime, timezone

DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DEFAULT_ONNX_MODEL_NAME = "Xenova/bge-small-zh-v1.5"
DEFAULT_DIM = 512
DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def default_doc_text(doc: dict) -> str:
    """Convenience default for what to embed per document: title + summary +
    headings, newline-joined, skipping empties. This is a reasonable
    starting choice (it's what the reference deployment this package was
    distilled from uses), not a requirement — embed_documents() takes
    whatever text you hand it per id, so pass your own extraction if a
    different field mix suits your corpus better."""
    parts = [doc.get("title", ""), doc.get("summary", "")] + list(doc.get("headings", []) or [])
    return "\n".join(p for p in parts if p)


def _default_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)

    def encode(texts):
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)

    return encode


def _load_manifest(manifest_path, model_name: str, dim: int) -> dict:
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"model": model_name, "dim": dim, "items": {}}


def _load_persisted_vectors(binary_path, manifest: dict, dim: int) -> dict:
    if not binary_path.exists():
        return {}
    raw = binary_path.read_bytes()
    vectors = {}
    for item_id, info in manifest.get("items", {}).items():
        off = info["index"] * dim * 4
        if off + dim * 4 > len(raw):
            continue  # stale manifest/binary mismatch -> treat as missing, will re-embed
        vectors[item_id] = struct.unpack_from(f"<{dim}f", raw, off)
    return vectors


def _embed_incremental(texts_by_id: dict, old_vectors: dict, old_meta: dict, model_name: str, force: bool, encoder=None):
    """Returns (vectors, hashes_by_id, to_embed) where `vectors` covers every
    id in `texts_by_id`, reusing old_vectors whenever the id's content hash
    is unchanged and re-embedding (in one batch call) only what's new or
    changed. `encoder` is an injectable `(texts: list[str]) -> list[vector]`
    function — pass one in tests to avoid downloading/running a real model;
    defaults to sentence_transformers.SentenceTransformer(model_name)."""
    hashes_by_id = {i: _content_hash(t) for i, t in texts_by_id.items()}
    to_embed = [
        i for i, h in hashes_by_id.items()
        if force or i not in old_vectors or old_meta.get(i, {}).get("hash") != h
    ]
    vectors = dict(old_vectors)
    if to_embed:
        encode = encoder or _default_encoder(model_name)
        batch_texts = [texts_by_id[i] for i in to_embed]
        encoded = encode(batch_texts)
        for i, vec in zip(to_embed, encoded):
            vectors[i] = tuple(float(x) for x in vec)
    return vectors, hashes_by_id, to_embed


def embed_documents(
    items,
    order,
    manifest_path,
    binary_path,
    dist_binary_path,
    dist_meta_path,
    model_name=DEFAULT_MODEL_NAME,
    onnx_model_name=DEFAULT_ONNX_MODEL_NAME,
    dim=DEFAULT_DIM,
    query_instruction=DEFAULT_QUERY_INSTRUCTION,
    build_id=None,
    force=False,
    encoder=None,
) -> dict:
    """items: list[{"id": str, "text": str}] — one record per document, text
    already extracted by the caller (see default_doc_text() for a reasonable
    default). order: list[str] of ids defining the dist/ output's vector
    order (typically your client-side document array's own iteration order)
    — an id missing from `items` or with empty text gets an all-zero vector
    in the dist/ output rather than being skipped, so `order`'s length always
    matches the output vector count."""
    manifest = _load_manifest(manifest_path, model_name, dim)
    old_meta = manifest.get("items", {})
    old_vectors = _load_persisted_vectors(binary_path, manifest, dim)

    texts_by_id = {it["id"]: it["text"] for it in items if (it.get("text") or "").strip()}
    vectors, hashes_by_id, to_embed = _embed_incremental(texts_by_id, old_vectors, old_meta, model_name, force, encoder)

    final_ids = sorted(i for i in texts_by_id if i in vectors)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    packed = bytearray()
    manifest_items = {}
    for idx, item_id in enumerate(final_ids):
        packed += struct.pack(f"<{dim}f", *vectors[item_id])
        manifest_items[item_id] = {"hash": hashes_by_id[item_id], "index": idx}
    binary_path.write_bytes(bytes(packed))
    manifest_path.write_text(json.dumps({
        "model": model_name, "onnx_model": onnx_model_name, "dim": dim,
        "query_instruction": query_instruction, "generated_at": _now(),
        "items": manifest_items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    dist_packed = bytearray()
    covered = 0
    for item_id in order:
        vec = vectors.get(item_id)
        if vec is None:
            vec = (0.0,) * dim
        else:
            covered += 1
        dist_packed += struct.pack(f"<{dim}f", *vec)
    dist_binary_path.parent.mkdir(parents=True, exist_ok=True)
    dist_binary_path.write_bytes(bytes(dist_packed))
    dist_meta_path.write_text(json.dumps({
        "schema_version": "0.1", "generated_at": _now(), "build_id": build_id,
        "model": onnx_model_name, "dim": dim, "query_instruction": query_instruction,
        "count": len(order), "covered": covered,
        "note": "Document-level vectors (L2-normalized). Vector i corresponds "
                "to order[i] -- keep `order` identical to your client-side "
                "document array's iteration order so no separate id map is "
                "needed. A document with no embeddable text has an all-zero "
                "vector; treat zero-norm as similarity 0, never divide by "
                "its norm.",
    }, ensure_ascii=False), encoding="utf-8")

    return {
        "total": len(order), "embeddable": len(texts_by_id),
        "embedded_now": len(to_embed), "covered": covered, "bytes": len(dist_packed),
    }


def embed_chunks(
    chunks,
    manifest_path,
    binary_path,
    dist_binary_path,
    dist_meta_path,
    model_name=DEFAULT_MODEL_NAME,
    onnx_model_name=DEFAULT_ONNX_MODEL_NAME,
    dim=DEFAULT_DIM,
    query_instruction=DEFAULT_QUERY_INSTRUCTION,
    max_chunks_per_doc=None,
    build_id=None,
    force=False,
    encoder=None,
) -> dict:
    """chunks: list[{"chunk_id": str, "doc_id": str, "heading": str|None,
    "text": str}] — typically build/chunk.py's chunk_document() output for
    every document, each record with a "doc_id" added. Unlike
    embed_documents(), a chunk with no text is simply excluded (there's no
    fixed-length `order` to preserve — the client aggregates chunk hits back
    to documents via the dist output's own doc_ids array, not by position)."""
    manifest = _load_manifest(manifest_path, model_name, dim)
    old_meta = manifest.get("items", {})
    old_vectors = _load_persisted_vectors(binary_path, manifest, dim)

    by_id = {c["chunk_id"]: c for c in chunks}
    texts_by_id = {cid: c["text"] for cid, c in by_id.items() if (c.get("text") or "").strip()}
    vectors, hashes_by_id, to_embed = _embed_incremental(texts_by_id, old_vectors, old_meta, model_name, force, encoder)

    final_ids = sorted(cid for cid in texts_by_id if cid in vectors)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    packed = bytearray()
    manifest_items = {}
    for idx, cid in enumerate(final_ids):
        packed += struct.pack(f"<{dim}f", *vectors[cid])
        manifest_items[cid] = {
            "hash": hashes_by_id[cid], "index": idx,
            "doc_id": by_id[cid].get("doc_id"), "heading": by_id[cid].get("heading"),
        }
    binary_path.write_bytes(bytes(packed))
    manifest_out = {
        "model": model_name, "onnx_model": onnx_model_name, "dim": dim,
        "query_instruction": query_instruction, "generated_at": _now(),
        "items": manifest_items,
    }
    if max_chunks_per_doc is not None:
        manifest_out["max_chunks_per_doc"] = max_chunks_per_doc
    manifest_path.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=1), encoding="utf-8")

    dist_packed = bytearray()
    doc_ids = []
    headings = []
    for cid in final_ids:
        dist_packed += struct.pack(f"<{dim}f", *vectors[cid])
        doc_ids.append(by_id[cid].get("doc_id"))
        headings.append(by_id[cid].get("heading"))
    dist_binary_path.parent.mkdir(parents=True, exist_ok=True)
    dist_binary_path.write_bytes(bytes(dist_packed))
    dist_meta = {
        "schema_version": "0.1", "generated_at": _now(), "build_id": build_id,
        "model": onnx_model_name, "dim": dim, "query_instruction": query_instruction,
        "count": len(final_ids), "docs_covered": len(set(doc_ids)),
        "doc_ids": doc_ids, "headings": headings,
        "note": "Chunk-level (section) vectors, L2-normalized. doc_ids[i]/"
                "headings[i] describe the document/section chunk vector i "
                "belongs to (headings[i] is null for a paragraph-fallback "
                "chunk with no heading) -- aggregate to a per-document score "
                "via max(chunk similarities). Complementary to document-"
                "level vectors: this set captures WHICH passage matched, not "
                "just that some part of the document did.",
    }
    if max_chunks_per_doc is not None:
        dist_meta["max_chunks_per_doc"] = max_chunks_per_doc
    dist_meta_path.write_text(json.dumps(dist_meta, ensure_ascii=False), encoding="utf-8")

    return {
        "total_chunks_input": len(chunks),
        "docs_with_chunks": len(set(doc_ids)),
        "total_chunks": len(final_ids),
        "embedded_now": len(to_embed),
        "bytes": len(dist_packed),
    }
