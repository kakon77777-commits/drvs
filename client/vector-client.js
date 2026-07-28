// DRVS — Dynamic Revealing Vector Search
// client/vector-client.js: browser-side semantic channel. Wraps in-browser
// WASM embedding inference (@huggingface/transformers) compared against
// pre-built document/chunk vectors (see build/embed.py), producing exactly
// the `semanticScores` Map that core/scoring.js's scoreCorpus() expects as
// its optional 5th argument.
//
// This is deliberately NOT a networked API: the model, vectors, and ONNX
// WASM runtime all load once from CDN/static-asset URLs (browser-cached
// after the first load), and every export here degrades to "no semantic
// scores" (an empty Map) rather than throwing — a model-load failure
// (offline, unsupported browser, low-memory mobile, or simply "not
// configured") can never break exact/lexical/dictionary/relation search,
// which is the whole point of keeping this channel structurally optional in
// core/scoring.js.
//
// createVectorClient(options) returns a fresh, independent client — no
// module-level singleton state — so a page can run more than one index
// (e.g. two different corpora) without them fighting over shared state.

const DEFAULT_MODEL_NAME = "Xenova/bge-small-zh-v1.5";

// Loaded from jsDelivr's `+esm` endpoint rather than a self-hosted copy of
// the npm package's dist file: that dist file contains internal bare module
// specifiers for optional backends (e.g. `import ... from
// "onnxruntime-web/webgpu"`) that a bundler resolves via node_modules/import
// maps at build time, but a browser loading it as a raw ES module cannot
// resolve on its own. jsDelivr's `+esm` build pre-resolves the whole
// dependency graph into one self-contained module, which is why
// @huggingface/transformers' own docs use exactly this CDN pattern for
// unbundled browser/worker usage instead of a raw dist-file copy.
const DEFAULT_LIBRARY_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.2.0/+esm";

// Corpus-calibrated defaults for BAAI/bge-small-zh-v1.5, taken from the
// project this package was distilled from (a ~1900-document zh-Hant
// technical corpus): a spread of queries from "clearly unrelated" (raw
// cosine maxing out ~0.47-0.48 even for the BEST-matching document) to
// "deeply, repeatedly covered topic" (~0.68 for genuinely on-topic hits).
// BGE's own model card explicitly warns raw cosine is not a fixed-threshold
// confidence value ("a similarity score greater than 0.5 does not indicate
// similar... what matters is relative order"), so these map the OBSERVED
// useful range onto [0,1] as corpus-calibrated absolute floor/ceiling
// constants — NOT a per-query relative rescale, which would wrongly inflate
// the "best of a bad bunch" for an off-topic query into looking like a
// strong semantic match.
//
// THESE MUST BE RECALIBRATED for a different embedding model or a corpus
// whose topical spread differs meaningfully from the above — see
// docs/ADAPTING_A_CORPUS.md for the recalibration recipe. Shipping the
// Chinese-technical-corpus defaults unchanged for, say, an English creative-
// writing corpus will silently mis-rank the semantic channel.
const DEFAULT_RAW_FLOOR = 0.50;
const DEFAULT_RAW_CEILING = 0.62;

// Shared cosine-similarity sweep against a flat Float32Array of stacked
// vectors. Returns Float32Array<normalizedScore> indexed like `count`;
// entries for a zero-norm vector (no embeddable text) are left as -1 (never
// a valid cosine value) so callers can distinguish "no signal" from "signal
// of 0".
function sweepCosine(q, flat, dim, count) {
  const out = new Float32Array(count).fill(-1);
  let qNorm = 0;
  for (let j = 0; j < dim; j++) qNorm += q[j] * q[j];
  if (qNorm < 1e-9) return out; // degenerate query embedding
  const qMag = Math.sqrt(qNorm);
  for (let i = 0; i < count; i++) {
    const off = i * dim;
    let dot = 0;
    let dNorm = 0;
    for (let j = 0; j < dim; j++) {
      const dv = flat[off + j];
      dot += q[j] * dv;
      dNorm += dv * dv;
    }
    if (dNorm < 1e-9) continue; // zero vector -> no signal
    out[i] = dot / (qMag * Math.sqrt(dNorm));
  }
  return out;
}

// options:
//   vectorsUrl / vectorsMetaUrl     required — doc-level vectors (see build/embed.py's embed_documents())
//   chunksUrl / chunksMetaUrl       optional — chunk-level vectors (see embed_chunks()); omit to skip
//                                   the chunk channel entirely (doc-level-only, same as not having built them)
//   modelName                        default: Xenova/bge-small-zh-v1.5 (ONNX build)
//   libraryUrl                       default: jsDelivr +esm build of @huggingface/transformers
//   rawFloor / rawCeiling            default: calibrated for the above model on a zh-Hant technical
//                                    corpus — RECALIBRATE for a different model/corpus, see module header
//   queryInstruction                 default: read from vectorsMeta.query_instruction at load time —
//                                    override only if you need to force a different instruction prefix
export function createVectorClient(options) {
  const opts = options || {};
  if (!opts.vectorsUrl || !opts.vectorsMetaUrl) {
    throw new Error("createVectorClient requires { vectorsUrl, vectorsMetaUrl }");
  }
  const modelName = opts.modelName || DEFAULT_MODEL_NAME;
  const libraryUrl = opts.libraryUrl || DEFAULT_LIBRARY_URL;
  const rawFloor = opts.rawFloor ?? DEFAULT_RAW_FLOOR;
  const rawCeiling = opts.rawCeiling ?? DEFAULT_RAW_CEILING;
  const chunksConfigured = !!(opts.chunksUrl && opts.chunksMetaUrl);

  let modelPromise = null;
  let vectorsPromise = null;
  let state = { status: "idle", error: null }; // idle -> loading -> ready | failed
  let chunkVectorsPromise = null;
  let chunkState = { status: chunksConfigured ? "idle" : "unconfigured", error: null };

  function normalizeRaw(raw) {
    if (raw <= rawFloor) return 0;
    if (raw >= rawCeiling) return 1;
    return (raw - rawFloor) / (rawCeiling - rawFloor);
  }

  async function loadModel() {
    const { pipeline } = await import(/* webpackIgnore: true */ libraryUrl);
    return pipeline("feature-extraction", modelName, { dtype: "q8" });
  }

  async function loadVectors() {
    const [metaRes, vecRes] = await Promise.all([fetch(opts.vectorsMetaUrl), fetch(opts.vectorsUrl)]);
    if (!metaRes.ok || !vecRes.ok) throw new Error("vector fetch failed");
    const meta = await metaRes.json();
    const buf = await vecRes.arrayBuffer();
    const flat = new Float32Array(buf);
    const dim = meta.dim;
    const count = flat.length / dim;
    if (!Number.isInteger(count)) throw new Error("vectors binary size does not match dim in meta");
    return { meta, flat, dim, count };
  }

  // Per-section vectors, complementary to the whole-document ones above —
  // captures WHICH passage matched, not just that some part of a long
  // document did. Optional on top of optional: if this fetch fails (or is
  // simply slower than the doc-vectors one), doc-level scoring alone still
  // works, same graceful-degradation contract as everything else in this
  // module. meta.doc_ids[i] is the source document for chunk vector i (see
  // build/embed.py's embed_chunks()) — callers need an id->index map (built
  // once from the full document list) to fold a chunk hit back into the
  // doc-index-keyed scores Map scoreCorpus expects; this module has no
  // document list of its own.
  async function loadChunkVectors() {
    const [metaRes, vecRes] = await Promise.all([fetch(opts.chunksMetaUrl), fetch(opts.chunksUrl)]);
    if (!metaRes.ok || !vecRes.ok) throw new Error("chunk vector fetch failed");
    const meta = await metaRes.json();
    const buf = await vecRes.arrayBuffer();
    const flat = new Float32Array(buf);
    const dim = meta.dim;
    const count = flat.length / dim;
    if (!Number.isInteger(count) || meta.doc_ids.length !== count) {
      throw new Error("chunk vectors binary size does not match dim/doc_ids in meta");
    }
    return { meta, flat, dim, count, docIds: meta.doc_ids, headings: meta.headings || [] };
  }

  // Kicks off model + vector loading in the background; never throws. Call
  // this once at Worker/page init so loading has a head start before the
  // first real search request. Chunk vectors load on their own independent
  // track: a chunk-fetch failure never touches `state`, only `chunkState` —
  // doc-level scoring must keep working even if chunk vectors 404, are slow,
  // or were never configured.
  function warmUp() {
    if (state.status === "idle") {
      state.status = "loading";
      modelPromise = loadModel().catch((e) => { state.status = "failed"; state.error = String(e); return null; });
      vectorsPromise = loadVectors().catch((e) => { state.status = "failed"; state.error = String(e); return null; });
      Promise.all([modelPromise, vectorsPromise]).then(([model, vectors]) => {
        if (model && vectors) state.status = "ready";
      });
    }
    if (chunksConfigured && chunkState.status === "idle") {
      chunkState.status = "loading";
      chunkVectorsPromise = loadChunkVectors().catch((e) => {
        chunkState.status = "failed"; chunkState.error = String(e); return null;
      });
      chunkVectorsPromise.then((chunks) => { if (chunks) chunkState.status = "ready"; });
    }
  }

  function semanticStatus() {
    return { ...state, chunks: { ...chunkState } };
  }

  // Returns a Map<doc_index, {score, source, heading}> (score: 0..1, post
  // floor/ceiling rescale, BEFORE the caller's own weights.semantic
  // multiplier; source: "doc" | "chunk"; heading: the matched section's
  // heading text, or null for a doc-level hit or a paragraph-fallback chunk
  // with no markdown heading) or an empty Map if the model/vectors are not
  // ready yet, not configured, or query embedding fails for any reason.
  // doc_index must match the SAME order used when building vectorsUrl's
  // binary (see build/embed.py) — typically your document array's own index.
  //
  // idToIndex (Map<doc_id, doc_index>, built once by the caller from the
  // already-loaded document list) is optional — pass it to also fold in
  // chunk-level hits (max chunk similarity per document), merged into the
  // SAME returned Map (the higher-scoring of doc-level vs. chunk-level wins
  // per doc) so scoreCorpus still only ever sees one merged semantic
  // channel. Omit it (or if chunk vectors simply aren't configured/ready
  // yet) and this behaves as doc-level-only — chunk scoring is strictly
  // additive, never required.
  async function semanticScores(rawQuery, idToIndex) {
    if (state.status === "idle") warmUp();
    if (state.status !== "ready") return new Map();
    if (!rawQuery || !rawQuery.trim()) return new Map();

    try {
      const [model, vectors] = await Promise.all([modelPromise, vectorsPromise]);
      if (!model || !vectors) return new Map();

      const instruction = opts.queryInstruction ?? vectors.meta.query_instruction ?? "";
      const output = await model(instruction + rawQuery, { pooling: "mean", normalize: true });
      const q = output.data; // Float32Array, length === vectors.dim

      const scores = new Map();
      const docCos = sweepCosine(q, vectors.flat, vectors.dim, vectors.count);
      for (let i = 0; i < docCos.length; i++) {
        const norm = normalizeRaw(docCos[i]);
        if (norm > 0) scores.set(i, { score: norm, source: "doc", heading: null });
      }

      if (idToIndex && chunkState.status === "ready" && chunkVectorsPromise) {
        const chunks = await chunkVectorsPromise;
        if (chunks) {
          const chunkCos = sweepCosine(q, chunks.flat, chunks.dim, chunks.count);
          for (let i = 0; i < chunkCos.length; i++) {
            if (chunkCos[i] < 0) continue;
            const docIndex = idToIndex.get(chunks.docIds[i]);
            if (docIndex === undefined) continue;
            const norm = normalizeRaw(chunkCos[i]);
            const existing = scores.get(docIndex);
            if (norm > 0 && (!existing || norm > existing.score)) {
              scores.set(docIndex, { score: norm, source: "chunk", heading: chunks.headings[i] || null });
            }
          }
        }
      }

      return scores;
    } catch {
      return new Map(); // never let a runtime embedding failure break the search
    }
  }

  return { warmUp, semanticStatus, semanticScores };
}
