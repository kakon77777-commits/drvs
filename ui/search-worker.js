// DRVS — Dynamic Revealing Vector Search
// ui/search-worker.js: owns the index and the scoring loop off the main
// thread, so typing stays responsive while a multi-thousand-document corpus
// is scored on every keystroke (and, when the semantic channel is on, while a
// WASM embedding model runs).
//
// This file is a thin harness, on purpose. All the ranking logic lives in
// core/scoring.js — which has no DOM, Worker, or fetch dependency and is
// therefore testable in plain Node. What runs in the browser is the exact
// code the tests exercise, not a parallel reimplementation of it.
//
// Configuration arrives in the `init` message rather than being hardcoded
// here, because a Worker script can't take constructor arguments. ui/reveal.js
// forwards whatever URLs the host configured; every one of them is optional
// except the index itself.
//
// Deploy note: the relative imports below resolve against THIS file's served
// URL, so the package's directory layout (ui/ next to core/ and client/) must
// be preserved when serving it. If you flatten or bundle the package, supply
// your own worker instead and keep this one as the reference implementation.

import { scoreCorpus, prepareIndex, DEFAULT_CONFIG } from "../core/scoring.js";

const DEFAULT_URLS = {
  indexUrl: "/drvs/index.json",
  configUrl: "/drvs/search.config.json",
  dictionaryUrl: "/drvs/dictionary.json",
  vectorsUrl: null,
  vectorsMetaUrl: null,
  chunksUrl: null,
  chunksMetaUrl: null,
};

let documents = null;
let dictionary = [];
let config = DEFAULT_CONFIG;
let meta = null;
let loadError = null;
let idToIndex = null;   // doc id -> array index, for aggregating chunk hits back to documents
let vectorClient = null;
let urls = { ...DEFAULT_URLS };

function mergeConfig(base, loaded) {
  if (!loaded) return base;
  const out = { ...base, ...loaded };
  for (const key of ["search", "tiers", "weights", "channels", "expansion_limits", "diversity", "display"]) {
    out[key] = { ...(base[key] || {}), ...(loaded[key] || {}) };
  }
  if (loaded.relationTypes) out.relationTypes = { ...(base.relationTypes || {}), ...loaded.relationTypes };
  return out;
}

async function fetchJson(url) {
  if (!url) return null;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// The semantic channel is loaded lazily and defensively: it is the only
// channel with a heavy (multi-megabyte, CDN-hosted) dependency, so a failure
// here must degrade to "no semantic scores" and nothing more. Exact, lexical,
// dictionary, and relation search never learn that this failed.
async function ensureVectorClient() {
  if (vectorClient !== null) return vectorClient;
  if (!urls.vectorsUrl || !urls.vectorsMetaUrl) { vectorClient = false; return false; }
  try {
    const { createVectorClient } = await import("../client/vector-client.js");
    vectorClient = createVectorClient({
      vectorsUrl: urls.vectorsUrl,
      vectorsMetaUrl: urls.vectorsMetaUrl,
      chunksUrl: urls.chunksUrl,
      chunksMetaUrl: urls.chunksMetaUrl,
      modelName: urls.modelName,
      libraryUrl: urls.libraryUrl,
      rawFloor: urls.rawFloor,
      rawCeiling: urls.rawCeiling,
    });
  } catch {
    vectorClient = false;
  }
  return vectorClient;
}

async function ensureLoaded() {
  if (documents || loadError) return;
  try {
    const idxRes = await fetch(urls.indexUrl);
    if (!idxRes.ok) throw new Error(`index fetch failed: ${idxRes.status}`);
    const idx = await idxRes.json();

    documents = prepareIndex(idx.documents || []);
    idToIndex = new Map(documents.map((d, i) => [d.i, i]));
    meta = {
      count: idx.count ?? documents.length,
      generated_at: idx.generated_at,
      build_id: idx.build_id,
      channels: idx.channels,
    };

    // Config and dictionary are both optional and both fail soft: a missing
    // config means built-in defaults, a missing dictionary means no query
    // expansion. Neither is worth failing a search over.
    const [loadedCfg, loadedDict] = await Promise.all([
      fetchJson(urls.configUrl),
      fetchJson(urls.dictionaryUrl),
    ]);
    config = mergeConfig(DEFAULT_CONFIG, loadedCfg);
    if (loadedDict) dictionary = loadedDict.entries || [];
  } catch (e) {
    loadError = String((e && e.message) || e);
    documents = [];
  }
}

self.onmessage = async (e) => {
  const { type, query, reqId, urls: initUrls } = e.data || {};

  if (type === "init") {
    if (initUrls) urls = { ...DEFAULT_URLS, ...initUrls };
    await ensureLoaded();
    // Start the model + vector download NOW rather than on the first search:
    // by the time the user finishes typing, it usually has a head start. Never
    // awaited — init must not block on a multi-megabyte model download, or the
    // whole search UI would appear frozen until it lands.
    if (!loadError && config.channels.semantic !== false) {
      ensureVectorClient().then((c) => { if (c) c.warmUp(); });
    }
    self.postMessage({ type: "ready", reqId, meta, error: loadError });
    return;
  }

  if (type === "search") {
    await ensureLoaded();
    if (loadError) {
      self.postMessage({ type: "error", reqId, error: loadError });
      return;
    }
    const t0 = Date.now();

    let vecScores = new Map();
    let vecStatus = null;
    if (config.channels.semantic !== false) {
      const client = await ensureVectorClient();
      if (client) {
        // semanticScores() resolves to an empty Map — never throws — if the
        // model isn't ready yet or failed to load, so awaiting it here can't
        // block or break the other four channels.
        vecScores = await client.semanticScores(query, idToIndex);
        vecStatus = client.semanticStatus();
      }
    }

    const result = scoreCorpus(documents, query, config, dictionary, vecScores);
    self.postMessage({
      type: "result",
      reqId,
      query,
      result,
      meta,
      latency_ms: Date.now() - t0,
      semantic: vecStatus ? { ...vecStatus, scored: vecScores.size } : { status: "off", scored: 0 },
    });
  }
};
